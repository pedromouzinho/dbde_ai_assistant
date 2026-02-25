"""Camada B — testes DevOps tools (mock-based, sem chamadas Azure reais)."""

from __future__ import annotations

import json
import time

import pytest


async def _noop_attach(*args, **kwargs):
    _ = (args, kwargs)
    return None


def _mock_batch_items():
    return {
        "value": [
            {
                "id": 1001,
                "fields": {
                    "System.Title": "Bug fix login",
                    "System.State": "Active",
                    "System.WorkItemType": "Bug",
                    "System.AreaPath": "IT.DIT\\DBDE",
                    "System.CreatedDate": "2026-02-01T10:00:00Z",
                    "System.AssignedTo": {"displayName": "Pedro"},
                    "System.CreatedBy": {"displayName": "Ana"},
                },
            },
            {
                "id": 1002,
                "fields": {
                    "System.Title": "Feature onboarding",
                    "System.State": "New",
                    "System.WorkItemType": "User Story",
                    "System.AreaPath": "IT.DIT\\DBDE",
                    "System.CreatedDate": "2026-02-02T10:00:00Z",
                    "System.AssignedTo": {"displayName": "João"},
                    "System.CreatedBy": {"displayName": "Marta"},
                },
            },
        ]
    }


async def _fake_devops_request(method, url, headers=None, json_body=None, **kwargs):
    _ = (method, headers)
    content_body = kwargs.get("content_body")

    if "wit/wiql" in url:
        return {"workItems": [{"id": 1001}, {"id": 1002}]}
    if "wit/workitemsbatch" in url:
        return _mock_batch_items()
    if "wit/workitems/$" in url:
        patch_doc = json.loads(content_body or "[]")
        title = ""
        for op in patch_doc:
            if op.get("path") == "/fields/System.Title":
                title = str(op.get("value", ""))
                break
        if title == "Teste":
            return {"error": "tipo inválido para política de teste"}
        return {"id": 4321, "_links": {"html": {"href": "https://dev.azure.com/mock/_workitems/edit/4321"}}}
    if "/wit/workitems/" in url and "fields=" in url:
        return {
            "id": 12345,
            "fields": {
                "System.Title": "US existente",
                "System.State": "Active",
                "System.WorkItemType": "User Story",
                "System.AreaPath": "IT.DIT\\DBDE",
                "System.Description": "<div>desc</div>",
                "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>ac</li></ul>",
                "System.Tags": "tag1;tag2",
            },
        }
    return {"error": f"unmocked url: {url}"}


async def _fake_llm_simple(*args, **kwargs):
    _ = (args, kwargs)
    return json.dumps(
        {
            "title": "US refinada",
            "description_html": "<div>Descrição refinada</div>",
            "acceptance_criteria_html": "<ul><li>Critério 1</li></ul>",
            "change_summary": "Ajuste de critérios",
        }
    )


@pytest.mark.asyncio
class TestDevOpsTools:
    async def test_query_workitems_basic(self, monkeypatch):
        import tools_devops

        monkeypatch.setattr(tools_devops, "devops_request_with_retry", _fake_devops_request)
        monkeypatch.setattr(tools_devops, "_attach_auto_csv_export", _noop_attach)

        result = await tools_devops.tool_query_workitems(
            "[System.WorkItemType] = 'Bug' AND [System.State] = 'Active'", top=10
        )
        assert "items" in result
        assert result["items_returned"] <= 10

    async def test_compute_kpi_distribution(self, monkeypatch):
        import tools_devops

        async def _fake_query(*args, **kwargs):
            _ = (args, kwargs)
            return {
                "total_count": 2,
                "items": [
                    {"state": "Active", "type": "Bug", "created_date": "2026-01-01"},
                    {"state": "New", "type": "User Story", "created_date": "2026-01-02"},
                ],
            }

        monkeypatch.setattr(tools_devops, "tool_query_workitems", _fake_query)
        result = await tools_devops.tool_compute_kpi("[System.State] <> ''", group_by="state", kpi_type="distribution")
        assert "state_distribution" in result
        assert result["total_count"] == 2

    async def test_create_workitem_requires_confirmation(self):
        import tools_devops

        result = await tools_devops.tool_create_workitem(title="US sem confirmação", confirmed=False)
        assert "error" in result

    async def test_refine_workitem_returns_refined_json(self, monkeypatch):
        import tools_devops

        monkeypatch.setattr(tools_devops, "devops_request_with_retry", _fake_devops_request)
        monkeypatch.setattr(tools_devops, "llm_simple", _fake_llm_simple)

        result = await tools_devops.tool_refine_workitem(work_item_id=12345, refinement_request="Adicionar validação")
        assert result.get("ready_to_apply") is True
        assert "refined" in result

    async def test_query_hierarchy_basic(self, monkeypatch):
        import tools_devops

        monkeypatch.setattr(tools_devops, "devops_request_with_retry", _fake_devops_request)
        monkeypatch.setattr(tools_devops, "_attach_auto_csv_export", _noop_attach)

        result = await tools_devops.tool_query_hierarchy(parent_id=999, child_type="User Story")
        assert "items" in result
        assert "total_count" in result

    async def test_scenarios_contract(self, monkeypatch, tool_scenarios):
        import tools_devops

        monkeypatch.setattr(tools_devops, "devops_request_with_retry", _fake_devops_request)
        monkeypatch.setattr(tools_devops, "llm_simple", _fake_llm_simple)
        monkeypatch.setattr(tools_devops, "_attach_auto_csv_export", _noop_attach)

        selected = [
            s
            for s in tool_scenarios["scenarios"]
            if s["tool"] in {"query_workitems", "create_workitem", "refine_workitem", "compute_kpi", "query_hierarchy"}
        ]

        async def _run_scenario(scenario):
            tool = scenario["tool"]
            args = scenario["input"]
            if tool == "query_workitems":
                return await tools_devops.tool_query_workitems(**args)
            if tool == "create_workitem":
                return await tools_devops.tool_create_workitem(**args)
            if tool == "refine_workitem":
                return await tools_devops.tool_refine_workitem(**args)
            if tool == "compute_kpi":
                return await tools_devops.tool_compute_kpi(**args)
            if tool == "query_hierarchy":
                return await tools_devops.tool_query_hierarchy(**args)
            return {"error": "unsupported"}

        for scenario in selected:
            start = time.perf_counter()
            result = await _run_scenario(scenario)
            elapsed_ms = (time.perf_counter() - start) * 1000.0

            if scenario["should_succeed"]:
                for key in scenario["expected_keys"]:
                    if scenario["tool"] == "compute_kpi" and key in {"kpi_type", "value"}:
                        continue
                    assert key in result, f"{scenario['id']} missing key {key}; got {result}"
            else:
                assert "error" in result, f"{scenario['id']} expected error; got {result}"

            assert elapsed_ms < scenario["timeout_ms"], (
                f"{scenario['id']} timeout: {elapsed_ms:.1f}ms >= {scenario['timeout_ms']}ms"
            )
