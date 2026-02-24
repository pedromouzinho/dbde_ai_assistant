#!/usr/bin/env python3
"""
Preflight de arquitetura para o Web App DBDE.

Objetivo:
- Verificar pré-requisitos operacionais antes de mudanças/deploy.
- Bloquear avanço quando faltam dependências críticas (deployments, segredos, settings).
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def _run_az(args: List[str]) -> Any:
    cmd = ["az", *args, "-o", "json"]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        msg = exc.output.strip() if exc.output else str(exc)
        raise RuntimeError(f"az command failed: {' '.join(shlex.quote(c) for c in cmd)}\n{msg}") from exc
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from az command: {' '.join(shlex.quote(c) for c in cmd)}") from exc


def _as_bool(value: str, default: bool = False) -> bool:
    txt = str(value or "").strip().lower()
    if not txt:
        return default
    return txt in {"1", "true", "yes", "on"}


def _extract_provider_spec(spec: str) -> Tuple[str, str]:
    if ":" in spec:
        p, m = spec.split(":", 1)
        return p.strip().lower(), m.strip()
    return "", spec.strip()


@dataclass
class Finding:
    level: str  # PASS / WARN / FAIL / INFO
    key: str
    detail: str


class Reporter:
    def __init__(self) -> None:
        self.rows: List[Finding] = []

    def add(self, level: str, key: str, detail: str) -> None:
        self.rows.append(Finding(level=level, key=key, detail=detail))

    def has_failures(self) -> bool:
        return any(r.level == "FAIL" for r in self.rows)

    def print(self) -> None:
        order = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}
        sorted_rows = sorted(self.rows, key=lambda r: (order.get(r.level, 9), r.key))
        for row in sorted_rows:
            print(f"[{row.level}] {row.key}: {row.detail}")


def _pick_aoai_account(
    app_settings: Dict[str, str],
    default_rg: str,
    explicit_rg: str,
    explicit_name: str,
) -> Tuple[Optional[str], Optional[str], List[str]]:
    notes: List[str] = []
    if explicit_name:
        return explicit_rg or default_rg, explicit_name, notes

    endpoint = str(app_settings.get("AZURE_OPENAI_ENDPOINT", "")).strip()
    host = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
    guessed = host.split(".")[0] if host else ""

    if not guessed:
        notes.append("AZURE_OPENAI_ENDPOINT ausente; não foi possível inferir conta AOAI.")
        return None, None, notes

    accounts = _run_az(["cognitiveservices", "account", "list"])
    for acc in accounts:
        if str(acc.get("name", "")) == guessed:
            rg = str(acc.get("resourceGroup", "")) or default_rg
            return rg, guessed, notes

    notes.append(f"Conta AOAI inferida ({guessed}) não encontrada na subscrição atual.")
    return None, guessed, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight architecture check for DBDE web app.")
    parser.add_argument("--resource-group", required=True, help="Resource group do Web App")
    parser.add_argument("--webapp", required=True, help="Nome do Web App")
    parser.add_argument("--aoai-resource-group", default="", help="Resource group do Azure OpenAI (opcional)")
    parser.add_argument("--aoai-account", default="", help="Nome da conta Azure OpenAI (opcional)")
    args = parser.parse_args()

    rep = Reporter()

    site_cfg = _run_az(["webapp", "config", "show", "-g", args.resource_group, "-n", args.webapp])
    app_settings_list = _run_az(["webapp", "config", "appsettings", "list", "-g", args.resource_group, "-n", args.webapp])
    app_settings = {str(x.get("name", "")): str(x.get("value", "")) for x in app_settings_list}

    # Core runtime checks
    if bool(site_cfg.get("alwaysOn")):
        rep.add("PASS", "always_on", "Always On ativo.")
    else:
        rep.add("FAIL", "always_on", "Always On desativado.")

    health_path = str(site_cfg.get("healthCheckPath", "") or "")
    if health_path == "/health":
        rep.add("PASS", "health_check", "Health check em /health.")
    else:
        rep.add("FAIL", "health_check", f"Health check incorreto: '{health_path or '<empty>'}'.")

    if str(app_settings.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")).strip():
        rep.add("PASS", "app_insights", "Application Insights configurado.")
    else:
        rep.add("WARN", "app_insights", "Application Insights sem connection string.")

    if str(app_settings.get("JWT_SECRET", "")).strip():
        rep.add("PASS", "jwt_secret", "JWT_SECRET configurado.")
    else:
        rep.add("FAIL", "jwt_secret", "JWT_SECRET ausente.")

    # Infra dependencies
    if str(app_settings.get("SEARCH_KEY", "")).strip():
        rep.add("PASS", "search_key", "SEARCH_KEY configurada.")
    else:
        rep.add("FAIL", "search_key", "SEARCH_KEY ausente.")

    if str(app_settings.get("STORAGE_CONNECTION_STRING", "")).strip():
        rep.add("PASS", "storage_connection", "STORAGE_CONNECTION_STRING configurada.")
    else:
        rep.add("FAIL", "storage_connection", "STORAGE_CONNECTION_STRING ausente.")

    # Scale-out visibility
    plans = _run_az(["appservice", "plan", "list", "-g", args.resource_group])
    if len(plans) == 1:
        workers = int((plans[0].get("numberOfWorkers") or 0))
        rep.add("INFO", "scale_out", f"Plano com {workers} instância(s).")
    else:
        rep.add("WARN", "scale_out", "Múltiplos plans no RG; validação de instâncias por app não determinística.")

    # AOAI deployments needed
    aoai_rg, aoai_name, notes = _pick_aoai_account(
        app_settings=app_settings,
        default_rg=args.resource_group,
        explicit_rg=args.aoai_resource_group,
        explicit_name=args.aoai_account,
    )
    for note in notes:
        rep.add("WARN", "aoai_discovery", note)

    needed_deployments: List[str] = []
    # Base explicit deployments
    chat_depl = str(app_settings.get("CHAT_DEPLOYMENT", "")).strip()
    emb_depl = str(app_settings.get("EMBEDDING_DEPLOYMENT", "")).strip()
    if chat_depl:
        needed_deployments.append(chat_depl)
    else:
        rep.add("WARN", "chat_deployment", "CHAT_DEPLOYMENT ausente em app settings (depende de default de código).")
    if emb_depl:
        needed_deployments.append(emb_depl)
    else:
        rep.add("WARN", "embedding_deployment", "EMBEDDING_DEPLOYMENT ausente em app settings (depende de default de código).")

    for key in ("LLM_TIER_FAST", "LLM_TIER_STANDARD", "LLM_TIER_PRO", "LLM_FALLBACK"):
        spec = str(app_settings.get(key, "")).strip()
        if not spec:
            rep.add("WARN", key.lower(), f"{key} ausente em app settings (depende de default de código).")
            continue
        provider, model = _extract_provider_spec(spec)
        if provider in ("", "azure_openai"):
            if model:
                needed_deployments.append(model)
            else:
                rep.add("FAIL", key.lower(), f"{key} inválido: '{spec}'.")
        else:
            rep.add("INFO", key.lower(), f"{key} usa provider externo '{provider}' (sem validação AOAI).")

    model_router_enabled = _as_bool(app_settings.get("MODEL_ROUTER_ENABLED", ""), default=False)
    if model_router_enabled:
        router_spec = str(app_settings.get("MODEL_ROUTER_SPEC", "")).strip()
        if not router_spec:
            rep.add("FAIL", "model_router_spec", "MODEL_ROUTER_ENABLED=true mas MODEL_ROUTER_SPEC vazio.")
        else:
            provider, model = _extract_provider_spec(router_spec)
            if provider in ("", "azure_openai") and model:
                needed_deployments.append(model)
            elif provider not in ("", "azure_openai"):
                rep.add("INFO", "model_router_spec", f"Router usa provider '{provider}'.")
            else:
                rep.add("FAIL", "model_router_spec", f"MODEL_ROUTER_SPEC inválido: '{router_spec}'.")

    # Rerank dependencies
    rerank_enabled = _as_bool(app_settings.get("RERANK_ENABLED", ""), default=False)
    if rerank_enabled:
        if str(app_settings.get("RERANK_ENDPOINT", "")).strip():
            rep.add("PASS", "rerank_endpoint", "RERANK_ENDPOINT configurado.")
        else:
            rep.add("FAIL", "rerank_endpoint", "RERANK_ENABLED=true mas RERANK_ENDPOINT ausente.")
        if str(app_settings.get("RERANK_API_KEY", "")).strip():
            rep.add("PASS", "rerank_api_key", "RERANK_API_KEY configurada.")
        else:
            rep.add("FAIL", "rerank_api_key", "RERANK_ENABLED=true mas RERANK_API_KEY ausente.")
    else:
        rep.add("INFO", "rerank", "RERANK_ENABLED=false.")

    if aoai_rg and aoai_name:
        deployments = _run_az(["cognitiveservices", "account", "deployment", "list", "-g", aoai_rg, "-n", aoai_name])
        existing = {str(d.get("name", "")).strip() for d in deployments}
        missing = sorted({x for x in needed_deployments if x and x not in existing})
        if missing:
            rep.add(
                "FAIL",
                "aoai_deployments",
                f"Deployments em falta: {', '.join(missing)} (conta {aoai_name}, rg {aoai_rg}).",
            )
        else:
            rep.add(
                "PASS",
                "aoai_deployments",
                f"Todos os deployments necessários existem ({len(set(needed_deployments))}).",
            )
    else:
        rep.add("WARN", "aoai_deployments", "Conta AOAI não resolvida; não foi possível validar deployments.")

    rep.print()
    return 2 if rep.has_failures() else 0


if __name__ == "__main__":
    sys.exit(main())

