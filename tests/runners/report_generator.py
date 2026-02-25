"""Geração de relatório HTML self-contained para eval suite."""

from __future__ import annotations

import html
from pathlib import Path


def _status_color(passed: bool) -> str:
    return "#1f9d55" if passed else "#c53030"


def _card(title: str, value: str, passed: bool) -> str:
    color = _status_color(passed)
    return (
        f"<div class='card'>"
        f"<h3>{html.escape(title)}</h3>"
        f"<p style='color:{color}'>{html.escape(value)}</p>"
        f"</div>"
    )


def generate_report(results: dict, output_path: str) -> None:
    """Gera relatório HTML com resumo das camadas."""
    run_id = str(results.get("run_id", "n/a"))
    timestamp = str(results.get("timestamp", "n/a"))
    mode = str(results.get("mode", "n/a"))
    layers = results.get("camadas", {}) if isinstance(results, dict) else {}

    cards = []
    table_rows = []
    for layer_name in ("a", "b", "c"):
        layer = layers.get(layer_name, {}) if isinstance(layers, dict) else {}
        passed = bool(layer.get("passed", False))
        returncode = layer.get("returncode", "n/a")
        label = f"Camada {layer_name.upper()}"
        cards.append(_card(label, f"returncode={returncode}", passed))
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{html.escape(str(returncode))}</td>"
            f"<td style='color:{_status_color(passed)}'>{'PASS' if passed else 'FAIL'}</td>"
            "</tr>"
        )

    summary = ""
    if layers:
        pass_count = sum(1 for x in layers.values() if x.get("passed"))
        total = len(layers)
        summary = f"{pass_count}/{total} camadas passaram"

    html_doc = f"""
<!doctype html>
<html lang="pt">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DBDE Eval Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; margin: 24px; color: #1a202c; }}
    h1, h2 {{ margin: 0 0 12px 0; }}
    .muted {{ color: #4a5568; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 16px 0 24px; }}
    .card {{ border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px; background: #f8fafc; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
    th, td {{ border: 1px solid #e2e8f0; padding: 8px; text-align: left; }}
    th {{ background: #edf2f7; }}
    .footer {{ margin-top: 24px; font-size: 12px; color: #718096; }}
  </style>
</head>
<body>
  <h1>DBDE Eval Suite Report</h1>
  <p class="muted">Run ID: <strong>{html.escape(run_id)}</strong> | Timestamp: <strong>{html.escape(timestamp)}</strong> | Modo: <strong>{html.escape(mode)}</strong></p>

  <h2>Dashboard</h2>
  <div class="grid">{''.join(cards)}</div>

  <h2>Resultados por Camada</h2>
  <table>
    <thead><tr><th>Camada</th><th>Return Code</th><th>Status</th></tr></thead>
    <tbody>{''.join(table_rows)}</tbody>
  </table>

  <h2>Summary</h2>
  <p>{html.escape(summary or 'Sem dados suficientes para resumo.')}</p>

  <div class="footer">Relatório gerado automaticamente pelo tests.runners.report_generator</div>
</body>
</html>
"""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_doc, encoding="utf-8")
