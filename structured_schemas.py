"""
Schemas JSON para Structured Outputs do Azure OpenAI.
Usados quando precisamos de respostas em formato previsível.
"""

SPRINT_ANALYSIS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "sprint_analysis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "sprint_name": {"type": "string"},
                "total_items": {"type": "integer"},
                "completed": {"type": "integer"},
                "in_progress": {"type": "integer"},
                "blocked": {"type": "integer"},
                "velocity": {"type": "number"},
                "health": {"type": "string", "enum": ["healthy", "at_risk", "critical"]},
                "summary": {"type": "string"},
                "risks": {"type": "array", "items": {"type": "string"}},
                "recommendations": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "sprint_name",
                "total_items",
                "completed",
                "in_progress",
                "blocked",
                "velocity",
                "health",
                "summary",
                "risks",
                "recommendations",
            ],
            "additionalProperties": False,
        },
    },
}

EMAIL_CLASSIFICATION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "email_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["urgent", "action_required", "informational", "fyi", "spam"],
                },
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "summary": {"type": "string"},
                "suggested_action": {"type": "string"},
                "requires_response": {"type": "boolean"},
            },
            "required": ["category", "priority", "summary", "suggested_action", "requires_response"],
            "additionalProperties": False,
        },
    },
}

DOCUMENT_ENTITIES_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "document_entities",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "document_type": {"type": "string"},
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "value": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["type", "value", "confidence"],
                        "additionalProperties": False,
                    },
                },
                "key_dates": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
            },
            "required": ["document_type", "entities", "key_dates", "summary"],
            "additionalProperties": False,
        },
    },
}

USER_STORY_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "user_story",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "as_a": {"type": "string"},
                "i_want": {"type": "string"},
                "so_that": {"type": "string"},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "test_scenarios": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "given": {"type": "string"},
                            "when": {"type": "string"},
                            "then": {"type": "string"},
                        },
                        "required": ["given", "when", "then"],
                        "additionalProperties": False,
                    },
                },
                "story_points": {"type": "integer"},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
            },
            "required": [
                "title",
                "as_a",
                "i_want",
                "so_that",
                "acceptance_criteria",
                "test_scenarios",
                "story_points",
                "priority",
            ],
            "additionalProperties": False,
        },
    },
}

DATA_TABLE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "data_table",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "total_rows": {"type": "integer"},
            },
            "required": ["title", "columns", "rows", "total_rows"],
            "additionalProperties": False,
        },
    },
}

# Schema específico já alinhado com output esperado da tool screenshot_to_us.
SCREENSHOT_USER_STORIES_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "screenshot_user_stories",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "stories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["title", "description", "acceptance_criteria"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["stories"],
            "additionalProperties": False,
        },
    },
}

