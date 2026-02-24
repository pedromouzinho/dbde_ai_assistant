# =============================================================================
# storage.py — Azure Table Storage operations v7.0
# =============================================================================
# REST API directo — sem SDK extra. SharedKeyLite auth.
# =============================================================================

import base64
import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import quote, unquote

import httpx

from config import (
    STORAGE_ACCOUNT,
    STORAGE_KEY,
    ADMIN_INITIAL_PASSWORD,
    UPLOAD_BLOB_CONTAINER_RAW,
    UPLOAD_BLOB_CONTAINER_TEXT,
    UPLOAD_BLOB_CONTAINER_CHUNKS,
    CHAT_TOOLRESULT_BLOB_CONTAINER,
    GENERATED_FILES_BLOB_CONTAINER,
)
from auth import hash_password

# Global HTTP client — inicializado pelo app.py no startup
http_client: Optional[httpx.AsyncClient] = None
logger = logging.getLogger(__name__)

# Fallback em memória se Table Storage falhar
feedback_memory: List[Dict] = []

# Tables que o sistema necessita
REQUIRED_TABLES = [
    "feedback",
    "examples",
    "AuditLog",
    "ChatHistory",
    "PromptRules",
    "Users",
    "WriterProfiles",
    "UploadJobs",
    "UploadIndex",
    "ExportJobs",
]
BLOB_API_VERSION = "2021-12-02"
BLOB_SERVICE_BASE_URL = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net"
BLOB_REQUIRED_CONTAINERS = [
    UPLOAD_BLOB_CONTAINER_RAW,
    UPLOAD_BLOB_CONTAINER_TEXT,
    UPLOAD_BLOB_CONTAINER_CHUNKS,
    CHAT_TOOLRESULT_BLOB_CONTAINER,
    GENERATED_FILES_BLOB_CONTAINER,
]


# =============================================================================
# AUTH HELPERS
# =============================================================================

def _table_auth_header(verb: str, table_path: str, date_str: str) -> str:
    """Gera SharedKeyLite auth header para Azure Table Storage."""
    string_to_sign = f"{date_str}\n/{STORAGE_ACCOUNT}/{table_path}"
    decoded_key = base64.b64decode(STORAGE_KEY)
    signature = base64.b64encode(
        hmac.new(decoded_key, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    return f"SharedKeyLite {STORAGE_ACCOUNT}:{signature}"


def _table_auth_header_raw(method: str, resource: str, date_str: str) -> str:
    """Gera SharedKeyLite auth header com resource path explícito."""
    string_to_sign = f"{date_str}\n{resource}"
    decoded_key = base64.b64decode(STORAGE_KEY)
    signature = base64.b64encode(
        hmac.new(decoded_key, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode()
    return f"SharedKeyLite {STORAGE_ACCOUNT}:{signature}"


def _odata_key_literal(value: str) -> str:
    """Escapa key OData e codifica para uso seguro em URL path."""
    escaped = str(value or "").replace("'", "''")
    return quote(escaped, safe="")


def _table_entity_path(table_name: str, partition_key: str, row_key: str) -> str:
    pk = _odata_key_literal(partition_key)
    rk = _odata_key_literal(row_key)
    return f"{table_name}(PartitionKey='{pk}',RowKey='{rk}')"


def _base_headers(auth: str, date_str: str, content_type: bool = False) -> dict:
    h = {
        "Authorization": auth,
        "x-ms-date": date_str,
        "x-ms-version": "2019-02-02",
        "Accept": "application/json;odata=nometadata",
    }
    if content_type:
        h["Content-Type"] = "application/json"
    return h


def _require_http_client() -> httpx.AsyncClient:
    if http_client is None:
        raise RuntimeError("storage http_client not initialized")
    return http_client


def _blob_canonicalized_headers(headers: dict) -> str:
    items = []
    for k, v in (headers or {}).items():
        lk = str(k).strip().lower()
        if not lk.startswith("x-ms-"):
            continue
        lv = " ".join(str(v).strip().split())
        items.append((lk, lv))
    items.sort(key=lambda x: x[0])
    return "".join(f"{k}:{v}\n" for k, v in items)


def _blob_canonicalized_resource(container: str, blob_name: str = "", query_params: Optional[dict] = None) -> str:
    resource = f"/{STORAGE_ACCOUNT}/{container}"
    if blob_name:
        resource += "/" + unquote(blob_name.lstrip("/"))
    if query_params:
        for key in sorted(query_params.keys()):
            resource += f"\n{str(key).lower()}:{str(query_params[key])}"
    return resource


def _blob_auth_header(
    method: str,
    container: str,
    blob_name: str = "",
    *,
    content_length: int = 0,
    content_type: str = "",
    ms_headers: Optional[dict] = None,
    query_params: Optional[dict] = None,
) -> str:
    ms_headers = ms_headers or {}
    canonicalized_headers = _blob_canonicalized_headers(ms_headers)
    canonicalized_resource = _blob_canonicalized_resource(container, blob_name, query_params)
    cl = "" if int(content_length or 0) == 0 else str(int(content_length))
    string_to_sign = (
        f"{method}\n"          # VERB
        "\n"                   # Content-Encoding
        "\n"                   # Content-Language
        f"{cl}\n"              # Content-Length
        "\n"                   # Content-MD5
        f"{content_type or ''}\n"  # Content-Type
        "\n"                   # Date
        "\n"                   # If-Modified-Since
        "\n"                   # If-Match
        "\n"                   # If-None-Match
        "\n"                   # If-Unmodified-Since
        "\n"                   # Range
        f"{canonicalized_headers}{canonicalized_resource}"
    )
    decoded_key = base64.b64decode(STORAGE_KEY)
    signature = base64.b64encode(
        hmac.new(decoded_key, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    return f"SharedKey {STORAGE_ACCOUNT}:{signature}"


def build_blob_ref(container: str, blob_name: str) -> str:
    return f"{container}/{blob_name.lstrip('/')}"


def parse_blob_ref(blob_ref: str) -> tuple[str, str]:
    ref = str(blob_ref or "").strip().lstrip("/")
    if "/" not in ref:
        return ref, ""
    container, blob_name = ref.split("/", 1)
    return container, blob_name


async def ensure_blob_container(container_name: str) -> bool:
    if not container_name:
        return False
    client = _require_http_client()
    now = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    ms_headers = {
        "x-ms-date": now,
        "x-ms-version": BLOB_API_VERSION,
    }
    query = {"restype": "container"}
    auth = _blob_auth_header(
        "PUT",
        container_name,
        "",
        content_length=0,
        content_type="",
        ms_headers=ms_headers,
        query_params=query,
    )
    headers = {
        **ms_headers,
        "Authorization": auth,
    }
    url = f"{BLOB_SERVICE_BASE_URL}/{quote(container_name)}"
    try:
        resp = await client.put(url, headers=headers, params=query)
        if resp.status_code in (201, 202, 409):
            return True
        logger.warning("[Storage] ensure_blob_container %s -> %s", container_name, resp.status_code)
        return False
    except Exception as e:
        logger.error("[Storage] ensure_blob_container failed: %s", e)
        return False


async def ensure_blob_containers() -> None:
    for container in BLOB_REQUIRED_CONTAINERS:
        ok = await ensure_blob_container(container)
        if ok:
            logger.info("  ✅ Blob container '%s' ready", container)
        else:
            logger.warning("  ⚠️ Blob container '%s' not ready", container)


async def blob_upload_bytes(
    container: str,
    blob_name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> dict:
    client = _require_http_client()
    payload = data if isinstance(data, (bytes, bytearray)) else bytes(data or b"")
    blob_name = blob_name.lstrip("/")
    now = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    ms_headers = {
        "x-ms-date": now,
        "x-ms-version": BLOB_API_VERSION,
        "x-ms-blob-type": "BlockBlob",
    }
    auth = _blob_auth_header(
        "PUT",
        container,
        blob_name,
        content_length=len(payload),
        content_type=content_type or "application/octet-stream",
        ms_headers=ms_headers,
    )
    headers = {
        **ms_headers,
        "Authorization": auth,
        "Content-Length": str(len(payload)),
        "Content-Type": content_type or "application/octet-stream",
    }
    url = f"{BLOB_SERVICE_BASE_URL}/{quote(container)}/{quote(blob_name, safe='/')}"
    resp = await client.put(url, headers=headers, content=payload)
    if resp.status_code not in (201, 202):
        raise RuntimeError(f"blob_upload_bytes failed: {resp.status_code} {resp.text[:200]}")
    return {
        "container": container,
        "blob_name": blob_name,
        "blob_ref": build_blob_ref(container, blob_name),
        "url": url,
        "etag": resp.headers.get("etag", ""),
        "size_bytes": len(payload),
    }


async def blob_upload_json(container: str, blob_name: str, payload: dict) -> dict:
    data = json.dumps(payload or {}, ensure_ascii=False, default=str).encode("utf-8")
    return await blob_upload_bytes(container, blob_name, data, content_type="application/json; charset=utf-8")


async def blob_download_bytes(container: str, blob_name: str) -> Optional[bytes]:
    client = _require_http_client()
    blob_name = blob_name.lstrip("/")
    now = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    ms_headers = {
        "x-ms-date": now,
        "x-ms-version": BLOB_API_VERSION,
    }
    auth = _blob_auth_header(
        "GET",
        container,
        blob_name,
        content_length=0,
        content_type="",
        ms_headers=ms_headers,
    )
    headers = {
        **ms_headers,
        "Authorization": auth,
    }
    url = f"{BLOB_SERVICE_BASE_URL}/{quote(container)}/{quote(blob_name, safe='/')}"
    resp = await client.get(url, headers=headers)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"blob_download_bytes failed: {resp.status_code} {resp.text[:200]}")
    return resp.content


async def blob_download_json(container: str, blob_name: str) -> Optional[dict]:
    raw = await blob_download_bytes(container, blob_name)
    if raw is None:
        return None
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8", errors="replace"))


# =============================================================================
# CRUD OPERATIONS
# =============================================================================

async def table_insert(table_name: str, entity: dict) -> bool:
    """Insere entidade numa Azure Table."""
    url = f"https://{STORAGE_ACCOUNT}.table.core.windows.net/{table_name}"
    date_str = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    auth = _table_auth_header("POST", table_name, date_str)
    
    try:
        resp = await http_client.post(
            url, headers=_base_headers(auth, date_str, content_type=True), json=entity
        )
        if resp.status_code in (201, 204):
            return True
        logger.error("Table insert error: %s - %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("[Storage] table_insert failed: %s", e)
        return False


async def table_query(table_name: str, filter_str: str = "", top: int = 50) -> list:
    """Query entidades de uma Azure Table."""
    url = f"https://{STORAGE_ACCOUNT}.table.core.windows.net/{table_name}()"
    date_str = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    auth = _table_auth_header("GET", f"{table_name}()", date_str)
    
    params = {"$top": str(top)}
    if filter_str:
        params["$filter"] = filter_str
    
    try:
        resp = await http_client.get(
            url, headers=_base_headers(auth, date_str), params=params
        )
        if resp.status_code == 200:
            return resp.json().get("value", [])
        logger.error("Table query error: %s - %s", resp.status_code, resp.text[:200])
        return []
    except Exception as e:
        logger.error("[Storage] table_query failed: %s", e)
        return []


async def table_merge(table_name: str, entity: dict):
    """Update/merge de uma entidade existente no Table Storage."""
    pk = entity["PartitionKey"]
    rk = entity["RowKey"]
    entity_path = _table_entity_path(table_name, pk, rk)
    url = f"https://{STORAGE_ACCOUNT}.table.core.windows.net/{entity_path}"
    date_str = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    
    resource = f"/{STORAGE_ACCOUNT}/{entity_path}"
    auth = _table_auth_header_raw("MERGE", resource, date_str)
    
    headers = _base_headers(auth, date_str, content_type=True)
    headers["If-Match"] = "*"
    
    body = {k: v for k, v in entity.items() if k not in ("PartitionKey", "RowKey")}
    
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request("MERGE", url, headers=headers, json=body)
        if resp.status_code not in (204, 200):
            raise Exception(f"Table merge failed: {resp.status_code} - {resp.text[:200]}")


async def table_delete(table_name: str, partition_key: str, row_key: str):
    """Apaga uma entidade do Table Storage."""
    entity_path = _table_entity_path(table_name, partition_key, row_key)
    url = f"https://{STORAGE_ACCOUNT}.table.core.windows.net/{entity_path}"
    date_str = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    
    resource = f"/{STORAGE_ACCOUNT}/{entity_path}"
    auth = _table_auth_header_raw("DELETE", resource, date_str)
    
    headers = _base_headers(auth, date_str)
    headers["If-Match"] = "*"
    
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(url, headers=headers)
        if resp.status_code not in (204, 200, 404):
            raise Exception(f"Table delete failed: {resp.status_code}")


# =============================================================================
# INITIALIZATION
# =============================================================================

async def ensure_tables_exist():
    """Cria as tabelas necessárias se não existirem."""
    for table_name in REQUIRED_TABLES:
        url = f"https://{STORAGE_ACCOUNT}.table.core.windows.net/Tables"
        date_str = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        auth = _table_auth_header("POST", "Tables", date_str)
        
        try:
            resp = await http_client.post(
                url, headers=_base_headers(auth, date_str, content_type=True),
                json={"TableName": table_name},
            )
            if resp.status_code == 201:
                logger.info("  ✅ Table '%s' created", table_name)
            elif resp.status_code == 409:
                logger.info("  ✅ Table '%s' already exists", table_name)
            else:
                logger.warning("  ⚠️ Table '%s': %s", table_name, resp.status_code)
        except Exception as e:
            logger.error("[Storage] ensure_tables_exist failed for table: %s", e)
    
    await _ensure_admin_user()


async def _ensure_admin_user():
    """Cria o admin user se não existir."""
    try:
        existing = await table_query(
            "Users", "PartitionKey eq 'user' and RowKey eq 'pedro.mousinho'", top=1
        )
        if not existing:
            initial_password = (ADMIN_INITIAL_PASSWORD or "").strip()
            if not initial_password:
                initial_password = secrets.token_urlsafe(16)
                logger.warning(
                    "[Storage] ADMIN_INITIAL_PASSWORD não definido. Password bootstrap gerada para 'pedro.mousinho'. "
                    "Define ADMIN_INITIAL_PASSWORD para controlo explícito e sem dependência de bootstrap automático.",
                )
            else:
                logger.info("[Storage] ADMIN_INITIAL_PASSWORD detectado para bootstrap do admin")
            entity = {
                "PartitionKey": "user",
                "RowKey": "pedro.mousinho",
                "DisplayName": "Pedro Mousinho",
                "PasswordHash": hash_password(initial_password),
                "Role": "admin",
                "CreatedAt": datetime.utcnow().isoformat(),
                "Active": True,
            }
            await table_insert("Users", entity)
            logger.info("  🔐 Admin user 'pedro.mousinho' created")
        else:
            logger.info("  🔐 Admin user 'pedro.mousinho' exists")
    except Exception as e:
        logger.error("[Storage] _ensure_admin_user failed: %s", e)


def init_http_client(client: httpx.AsyncClient):
    """Chamado pelo app.py no startup para injectar o http client."""
    global http_client
    http_client = client
