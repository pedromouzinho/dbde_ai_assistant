#!/usr/bin/env python3
"""Smoke test — valida endpoints criticos do DBDE AI Assistant.

Uso:
    python scripts/smoke_test.py [BASE_URL]

Se BASE_URL nao for fornecido, usa http://localhost:8000.
Exit code 0 = todos os checks passaram. Exit code 1 = pelo menos um falhou.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "http://localhost:8000"


def check(name: str, url: str, expected_status: int = 200, must_contain: str | None = None) -> bool:
    """Executa um health check e retorna True/False."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
            if status != expected_status:
                print(f"  FAIL {name}: expected {expected_status}, got {status}")
                return False
            if must_contain and must_contain not in body:
                print(f"  FAIL {name}: response missing '{must_contain}'")
                return False
            print(f"  OK   {name} ({status})")
            return True
    except Exception as exc:
        print(f"  FAIL {name}: {exc}")
        return False


def main() -> int:
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else DEFAULT_BASE
    print(f"Smoke test: {base}\n")

    results = [
        check("GET /health", f"{base}/health", 200, '"status"'),
        check("GET /health?deep=true", f"{base}/health?deep=true", 200, '"status"'),
        check("GET /api/info", f"{base}/api/info", 200, '"version"'),
        check("GET / (frontend)", f"{base}/", 200, "<div id"),
    ]

    passed = sum(results)
    total = len(results)
    print(f"\nResultado: {passed}/{total} passed")

    if passed < total:
        print("SMOKE TEST FAILED — nao fazer swap!")
        return 1

    print("SMOKE TEST PASSED — seguro para swap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
