"""Runner principal do eval suite DBDE AI Assistant."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import eval_config
from .report_generator import generate_report


def _runner_timeout_seconds() -> int:
    raw = os.getenv("EVAL_RUNNER_TIMEOUT_SECONDS", "").strip()
    if raw:
        try:
            return max(60, int(raw))
        except ValueError:
            pass
    return 300 if eval_config.MOCK_LLM else 1200


def run_pytest(test_path: str, extra_args: list | None = None) -> dict:
    """Corre pytest num path e captura resultados."""
    timeout_seconds = _runner_timeout_seconds()
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        test_path,
        "--tb=short",
        "-q",
        "--json-report",
    ]
    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
        # fallback se plugin pytest-json-report não estiver instalado
        if "unrecognized arguments: --json-report" in (result.stderr or ""):
            fallback_cmd = [
                sys.executable,
                "-m",
                "pytest",
                test_path,
                "--tb=short",
                "-q",
            ]
            if extra_args:
                fallback_cmd.extend(extra_args)
            result = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except Exception as exc:
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": f"runner exception: {exc}",
            "passed": False,
        }

    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "passed": result.returncode == 0,
    }


def _dry_run_validate_datasets() -> None:
    for ds in ["rag_golden_set.json", "tool_scenarios.json", "arena_prompts.json"]:
        path = eval_config.DATASETS_DIR / ds
        assert path.exists(), f"Dataset missing: {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        count = len(data.get("entries", data.get("scenarios", data.get("prompts", []))))
        print(f"  ✓ {ds}: {count} entries")
    print("\n✓ Dry run passed — all datasets valid")


def main() -> None:
    parser = argparse.ArgumentParser(description="DBDE Eval Suite Runner")
    parser.add_argument("--dry-run", action="store_true", help="Só valida estrutura")
    parser.add_argument("--camada", choices=["a", "b", "c", "all"], default="all")
    parser.add_argument("--output", default=None, help="Ficheiro de output (JSON)")
    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results = {
        "run_id": f"eval_{timestamp}",
        "timestamp": timestamp,
        "mode": "dry_run" if args.dry_run else ("mock" if eval_config.MOCK_LLM else "real"),
        "camadas": {},
    }

    if args.dry_run:
        _dry_run_validate_datasets()
        return

    test_root = str(eval_config.EVAL_ROOT)

    if args.camada in ("a", "all"):
        print("\n=== CAMADA A: RAG Quality ===")
        results["camadas"]["a"] = run_pytest(f"{test_root}/camada_a_rag/")

    if args.camada in ("b", "all"):
        print("\n=== CAMADA B: Tool Eval ===")
        results["camadas"]["b"] = run_pytest(f"{test_root}/camada_b_tools/")

    if args.camada in ("c", "all"):
        print("\n=== CAMADA C: Arena ===")
        results["camadas"]["c"] = run_pytest(f"{test_root}/camada_c_arena/")

    output_path = args.output or str(eval_config.RESULTS_DIR / f"eval_{timestamp}.json")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✓ Resultados guardados em: {output_path}")

    html_path = output_path.replace(".json", ".html")
    generate_report(results, html_path)
    print(f"✓ Relatório HTML guardado em: {html_path}")


if __name__ == "__main__":
    main()
