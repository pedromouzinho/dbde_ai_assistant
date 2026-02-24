# =============================================================================
# tool_registry.py — Registry dinâmico de tools
# =============================================================================

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional


ToolHandler = Callable[[Dict[str, Any]], Awaitable[Any] | Any]

_handlers: Dict[str, ToolHandler] = {}
_definitions: Dict[str, dict] = {}


def register_tool(name: str, handler: ToolHandler, definition: Optional[dict] = None) -> None:
    """Regista/atualiza uma tool no registry global."""
    tool_name = str(name or "").strip()
    if not tool_name:
        raise ValueError("Tool name vazio")
    _handlers[tool_name] = handler
    if definition is not None:
        _definitions[tool_name] = definition


def has_tool(name: str) -> bool:
    return str(name or "").strip() in _handlers


def get_all_tool_definitions() -> List[dict]:
    return list(_definitions.values())


def get_registered_tool_names() -> List[str]:
    return list(_handlers.keys())


async def execute_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
    fn = _handlers.get(str(tool_name or "").strip())
    if not fn:
        return {"error": f"Tool desconhecida: {tool_name}"}
    args = arguments or {}
    try:
        result = fn(args)
        if inspect.isawaitable(result):
            return await result
        return result
    except Exception as e:
        logging.error("[ToolRegistry] execute_tool failed (%s): %s", tool_name, e, exc_info=True)
        return {"error": f"Erro ao executar tool {tool_name}: {str(e)}"}

