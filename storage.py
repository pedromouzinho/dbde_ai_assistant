# =============================================================================
# storage.py — Azure Table Storage operations v7.0
# =============================================================================
# REST API directo — sem SDK extra. SharedKeyLite auth.
# =============================================================================

import base64
import hashlib
import hmac
import logging
from datetime import datetime
from typing import List, Dict, Optional

import httpx

from config import STORAGE_ACCOUNT, STORAGE_KEY
from auth import hash_password

# Global HTTP client — inicializado pelo app.py no startup
http_client: Optional[httpx.AsyncClient] = None
logger = logging.getLogger(__name__)

# Fallback em memória se Table Storage falhar
feedback_memory: List[Dict] = []

# Tables que o sistema necessita
REQUIRED_TABLES = ["feedback", "examples", "AuditLog", "ChatHistory", "PromptRules", "Users"]


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
    url = f"https://{STORAGE_ACCOUNT}.table.core.windows.net/{table_name}(PartitionKey='{pk}',RowKey='{rk}')"
    date_str = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    
    resource = f"/{STORAGE_ACCOUNT}/{table_name}(PartitionKey='{pk}',RowKey='{rk}')"
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
    url = (
        f"https://{STORAGE_ACCOUNT}.table.core.windows.net/"
        f"{table_name}(PartitionKey='{partition_key}',RowKey='{row_key}')"
    )
    date_str = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    
    resource = f"/{STORAGE_ACCOUNT}/{table_name}(PartitionKey='{partition_key}',RowKey='{row_key}')"
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
                logger.error("  ✅ Table '%s' created", table_name)
            elif resp.status_code == 409:
                logger.error("  ✅ Table '%s' already exists", table_name)
            else:
                logger.error("  ⚠️ Table '%s': %s", table_name, resp.status_code)
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
            entity = {
                "PartitionKey": "user",
                "RowKey": "pedro.mousinho",
                "DisplayName": "Pedro Mousinho",
                "PasswordHash": hash_password("Millennium2026!"),
                "Role": "admin",
                "CreatedAt": datetime.utcnow().isoformat(),
                "Active": True,
            }
            await table_insert("Users", entity)
            logger.error("  🔐 Admin user 'pedro.mousinho' created")
        else:
            logger.error("  🔐 Admin user 'pedro.mousinho' exists")
    except Exception as e:
        logger.error("[Storage] _ensure_admin_user failed: %s", e)


def init_http_client(client: httpx.AsyncClient):
    """Chamado pelo app.py no startup para injectar o http client."""
    global http_client
    http_client = client
