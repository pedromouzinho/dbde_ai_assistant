from __future__ import annotations

import base64

from llm_provider import _openai_messages_to_anthropic


def test_openai_messages_to_anthropic_converts_data_url_images():
    payload = base64.b64encode(b"fake-png").decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analisa este ecrã."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{payload}"}},
            ],
        }
    ]

    system_prompt, anthropic_messages = _openai_messages_to_anthropic(messages)

    assert system_prompt == ""
    assert anthropic_messages[0]["role"] == "user"
    content = anthropic_messages[0]["content"]
    assert content[0] == {"type": "text", "text": "Analisa este ecrã."}
    assert content[1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": payload,
        },
    }


def test_openai_messages_to_anthropic_replaces_unsupported_image_formats_with_text_notice():
    payload = base64.b64encode(b"<svg></svg>").decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analisa este SVG."},
                {"type": "image_url", "image_url": {"url": f"data:image/svg+xml;base64,{payload}"}},
            ],
        }
    ]

    _, anthropic_messages = _openai_messages_to_anthropic(messages)
    content = anthropic_messages[0]["content"]

    assert content[0] == {"type": "text", "text": "Analisa este SVG."}
    assert content[1]["type"] == "text"
    assert "formato não suportado" in content[1]["text"]
