#!/usr/bin/env python3
"""Smoke test — valida endpoints críticos do DBDE AI Assistant.

Uso:
    python scripts/smoke_test.py [BASE_URL]
    SMOKE_USER=admin SMOKE_PASS=xxx python scripts/smoke_test.py [BASE_URL]

Se BASE_URL não for fornecido, usa http://localhost:8000.
Exit code 0 = todos os checks passaram. Exit code 1 = pelo menos um falhou.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

DEFAULT_BASE = "http://localhost:8000"


def check(
    name: str,
    url: str,
    expected_status: int = 200,
    must_contain: str | None = None,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict | None = None,
    accept_statuses: list[int] | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> bool:
    """Executa um check HTTP e retorna True/False."""
    try:
        req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
        execute = opener.open if opener is not None else urllib.request.urlopen
        try:
            with execute(req, timeout=30) as resp:
                status = resp.status
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            status = e.code
            body = e.read().decode("utf-8", errors="replace")

        ok_statuses = accept_statuses or [expected_status]
        if status not in ok_statuses:
            print(f"  FAIL {name}: expected {ok_statuses}, got {status}")
            return False
        if must_contain and must_contain not in body:
            print(f"  FAIL {name}: response missing '{must_contain}'")
            return False
        print(f"  OK   {name} ({status})")
        return True
    except Exception as exc:
        print(f"  FAIL {name}: {exc}")
        return False


def login_with_cookie(base: str, user: str, password: str) -> urllib.request.OpenerDirector | None:
    """Efetua login e devolve opener com cookie jar."""
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    payload = json.dumps({"username": user, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/auth/login",
        method="POST",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(req, timeout=20) as resp:
            if resp.status != 200:
                print(f"  FAIL POST /api/auth/login: status {resp.status}")
                return None
        has_auth_cookie = any(c.name == "dbde_token" for c in jar)
        if not has_auth_cookie:
            print("  FAIL POST /api/auth/login: cookie dbde_token não encontrado")
            return None
        print("  OK   POST /api/auth/login (200)")
        return opener
    except Exception as exc:
        print(f"  FAIL POST /api/auth/login: {exc}")
        return None


def main() -> int:
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else DEFAULT_BASE
    print(f"Smoke test: {base}\n")

    print("--- Unauthenticated ---")
    results = [
        check("GET /health", f"{base}/health", 200, '"status"'),
        check("GET /api/info", f"{base}/api/info", 200, '"version"'),
        check("GET / (frontend)", f"{base}/", 200, "<div id"),
    ]

    user = os.environ.get("SMOKE_USER", "").strip()
    password = os.environ.get("SMOKE_PASS", "").strip()
    if user and password:
        print("\n--- Authenticated ---")
        opener = login_with_cookie(base, user, password)
        if opener is None:
            results.append(False)
        else:
            results.append(
                check(
                    "GET /health?deep=true",
                    f"{base}/health?deep=true",
                    200,
                    '"checks"',
                    opener=opener,
                )
            )
            chat_payload = json.dumps(
                {
                    "question": "ping",
                    "conversation_id": "__smoke_test__",
                    "mode": "general",
                    "model_tier": "fast",
                }
            ).encode("utf-8")
            results.append(
                check(
                    "POST /chat/agent",
                    f"{base}/chat/agent",
                    200,
                    method="POST",
                    data=chat_payload,
                    headers={"Content-Type": "application/json"},
                    accept_statuses=[200, 429],
                    opener=opener,
                )
            )
            results.append(
                check(
                    "GET /api/upload/jobs",
                    f"{base}/api/upload/jobs",
                    200,
                    must_contain='"jobs"',
                    opener=opener,
                )
            )
    else:
        print("\n--- Skipping auth tests (set SMOKE_USER and SMOKE_PASS) ---")

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\nResultado: {passed}/{total} passed")
    if passed < total:
        print("SMOKE TEST FAILED — nao fazer swap!")
        return 1

    print("SMOKE TEST PASSED — seguro para swap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
