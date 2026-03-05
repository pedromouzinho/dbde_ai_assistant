"""Tests para guardrail de routing em análise de ficheiros tabulares."""


def test_forced_uploaded_table_call_for_analysis_intent(monkeypatch):
    import agent

    monkeypatch.setattr(
        agent,
        "_get_uploaded_files",
        lambda _conv_id: [{"filename": "Sample_Tbl_Contact_Detail.xlsx"}],
    )

    calls = agent._extract_forced_uploaded_table_calls(
        "analisa este ficheiro e dá-me a lista completa dos valores distintos",
        "conv-1",
        already_used=[],
    )
    assert len(calls) == 1
    call = calls[0]
    assert call.name == "analyze_uploaded_table"
    assert call.arguments.get("full_points") is True


def test_no_forced_uploaded_table_call_for_non_analysis_intent(monkeypatch):
    import agent

    monkeypatch.setattr(
        agent,
        "_get_uploaded_files",
        lambda _conv_id: [{"filename": "Sample_Tbl_Contact_Detail.xlsx"}],
    )

    calls = agent._extract_forced_uploaded_table_calls(
        "olá, tudo bem?",
        "conv-1",
        already_used=[],
    )
    assert calls == []
