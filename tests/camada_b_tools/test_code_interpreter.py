"""Tests para code interpreter sandbox."""

from __future__ import annotations

import pytest

from code_interpreter import execute_code, _validate_code


def test_validate_empty():
    assert _validate_code("") is not None


def test_validate_blocked_import():
    assert _validate_code("import subprocess") is not None
    assert _validate_code("import socket") is not None


def test_validate_allowed_import():
    assert _validate_code("import pandas") is None
    assert _validate_code("import math") is None


def test_validate_dangerous_function():
    assert _validate_code("os.system('rm -rf /')") is not None
    assert _validate_code("eval('malicious')") is not None


@pytest.mark.asyncio
async def test_basic_execution():
    result = await execute_code("print(2 + 2)")
    assert result["success"]
    assert "4" in (result.get("stdout") or "")


@pytest.mark.asyncio
async def test_pandas():
    pytest.importorskip("pandas")
    code = """
import pandas as pd
df = pd.DataFrame({"a": [1,2,3], "b": [4,5,6]})
print(df.to_string())
"""
    result = await execute_code(code)
    assert result["success"]
    assert "a" in (result.get("stdout") or "")


@pytest.mark.asyncio
async def test_timeout(monkeypatch):
    monkeypatch.setattr("code_interpreter.CODE_INTERPRETER_TIMEOUT", 1)
    result = await execute_code("import time; time.sleep(60)")
    assert not result["success"]
    assert "Timeout" in str(result.get("error", ""))
