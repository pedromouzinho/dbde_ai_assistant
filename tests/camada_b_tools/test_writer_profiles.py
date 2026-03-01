"""Tests para WriterProfiles tool wiring (SPEC-20)."""

import inspect


class TestWriterProfileWiring:
    def test_get_writer_profile_in_definitions(self):
        """Verificar que get_writer_profile está no schema."""
        from tools import _TOOL_DEFINITION_BY_NAME

        tool_def = _TOOL_DEFINITION_BY_NAME.get("get_writer_profile")
        assert tool_def is not None
        params = tool_def["function"]["parameters"]["properties"]
        assert "author_name" in params

    def test_save_writer_profile_in_definitions(self):
        """Verificar que save_writer_profile está no schema."""
        from tools import _TOOL_DEFINITION_BY_NAME

        tool_def = _TOOL_DEFINITION_BY_NAME.get("save_writer_profile")
        assert tool_def is not None
        params = tool_def["function"]["parameters"]["properties"]
        assert "author_name" in params
        assert "analysis" in params

    def test_writer_profile_tools_in_dispatch(self):
        """Verificar que ambas as tools estão no dispatch."""
        from tools import _tool_dispatch

        dispatch = _tool_dispatch()
        assert "get_writer_profile" in dispatch
        assert "save_writer_profile" in dispatch

    def test_tool_get_writer_profile_signature(self):
        """Verificar assinatura da função."""
        from tools_learning import tool_get_writer_profile

        sig = inspect.signature(tool_get_writer_profile)
        assert "author_name" in sig.parameters

    def test_tool_save_writer_profile_signature(self):
        """Verificar assinatura da função."""
        from tools_learning import tool_save_writer_profile

        sig = inspect.signature(tool_save_writer_profile)
        assert "author_name" in sig.parameters
        assert "analysis" in sig.parameters
        assert "preferred_vocabulary" in sig.parameters
