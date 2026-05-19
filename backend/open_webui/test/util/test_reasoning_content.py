import json

import pytest

from open_webui.utils.misc import extract_openai_message_content
from open_webui.utils.response import (
    convert_response_ollama_to_openai,
    convert_streaming_response_ollama_to_openai,
)


def test_extract_openai_message_content_renders_separate_reasoning_field():
    """Renders separate reasoning_content into the existing reasoning details block."""
    message = {
        "content": "Final answer",
        "reasoning_content": "Check inputs\nCompare options",
    }

    content = extract_openai_message_content(
        message,
        include_reasoning=True,
        reasoning_done=True,
        reasoning_duration=3,
    )

    assert content is not None
    assert '<details type="reasoning" done="true" duration="3">' in content
    assert "<summary>Thought for 3 seconds</summary>" in content
    assert "> Check inputs" in content
    assert "> Compare options" in content
    assert content.endswith("Final answer")


def test_extract_openai_message_content_flattens_list_payloads():
    """Flattens list-based content and reasoning payloads into assistant-visible text."""
    message = {
        "content": [{"type": "output_text", "text": "Final answer"}],
        "reasoning_content": [
            {"type": "reasoning_text", "text": "Plan"},
            {"type": "reasoning_text", "text": "Verify"},
        ],
    }

    content = extract_openai_message_content(message, include_reasoning=True)

    assert content is not None
    assert "> PlanVerify" in content
    assert content.endswith("Final answer")


def test_extract_openai_message_content_marks_done_reasoning_without_duration():
    """Uses a completed summary label when reasoning is done but duration is unknown."""
    message = {
        "content": "Final answer",
        "reasoning_content": "Check inputs",
    }

    content = extract_openai_message_content(message, include_reasoning=True)

    assert content is not None
    assert '<details type="reasoning" done="true">' in content
    assert "<summary>Thought process</summary>" in content
    assert "Thinking…" not in content


def test_convert_response_ollama_to_openai_maps_thinking_to_reasoning_content():
    """Maps Ollama's thinking field onto reasoning_content in non-streaming responses."""
    response = convert_response_ollama_to_openai(
        {
            "model": "qwen3.5:0.8b",
            "message": {
                "role": "assistant",
                "content": "Final answer",
                "thinking": "First think, then answer",
            },
        }
    )

    message = response["choices"][0]["message"]

    assert message["content"] == "Final answer"
    assert message["reasoning_content"] == "First think, then answer"


@pytest.mark.anyio
async def test_convert_streaming_response_ollama_to_openai_emits_reasoning_and_content_chunks():
    """Preserves Ollama thinking and answer text in separate streaming OpenAI chunks."""

    class FakeResponse:
        def __init__(self, chunks):
            self.body_iterator = self._iterate(chunks)

        async def _iterate(self, chunks):
            for chunk in chunks:
                yield json.dumps(chunk).encode("utf-8")

    response = FakeResponse(
        [
            {
                "model": "qwen3.5:0.8b",
                "message": {"thinking": "Plan step 1", "content": ""},
                "done": False,
            },
            {
                "model": "qwen3.5:0.8b",
                "message": {"thinking": "", "content": "Final answer"},
                "done": False,
            },
            {
                "model": "qwen3.5:0.8b",
                "message": {"thinking": "", "content": ""},
                "done": True,
                "eval_count": 10,
                "eval_duration": 10_000_000,
                "prompt_eval_count": 5,
                "prompt_eval_duration": 10_000_000,
                "total_duration": 2_000_000_000,
                "load_duration": 100,
            },
        ]
    )

    lines = [
        line async for line in convert_streaming_response_ollama_to_openai(response)
    ]

    reasoning_chunk = json.loads(lines[0][len("data: ") :].strip())
    content_chunk = json.loads(lines[1][len("data: ") :].strip())
    done_chunk = json.loads(lines[2][len("data: ") :].strip())

    assert reasoning_chunk["choices"][0]["delta"] == {
        "reasoning_content": "Plan step 1"
    }
    assert content_chunk["choices"][0]["delta"] == {"content": "Final answer"}
    assert done_chunk["choices"][0]["finish_reason"] == "stop"
    assert "delta" not in done_chunk["choices"][0]
    assert done_chunk["usage"]["eval_count"] == 10
    assert lines[-1] == "data: [DONE]\n\n"
