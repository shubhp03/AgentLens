import asyncio
from types import SimpleNamespace

from agentlens_agent.llm.anthropic_client import (
    AnthropicClient,
    parse_anthropic_response,
    to_anthropic_messages,
)
from agentlens_agent.llm.base import Message, ToolCall, ToolResult


def test_message_translation_shapes():
    msgs = [
        Message(role="user", text="q"),
        Message(
            role="assistant",
            text="thinking",
            tool_calls=(ToolCall("t1", "calculate", {"expression": "1+1"}),),
        ),
        Message(role="tool", tool_results=(ToolResult("t1", "2", is_error=False),)),
    ]
    out = to_anthropic_messages(msgs)
    assert out[0] == {"role": "user", "content": "q"}
    assert out[1]["content"][1]["type"] == "tool_use" and out[1]["content"][1]["id"] == "t1"
    assert out[2]["role"] == "user" and out[2]["content"][0]["tool_use_id"] == "t1"


def test_parse_response_with_tool_use_and_usage():
    resp = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="hello"),
            SimpleNamespace(
                type="tool_use", id="t9", name="calculate", input={"expression": "2*3"}
            ),
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=4, cache_read_input_tokens=None),
        stop_reason="tool_use",
    )
    r = parse_anthropic_response(resp, 12.5)
    assert r.text == "hello" and r.tool_calls[0].arguments == {"expression": "2*3"}
    assert (r.usage.input_tokens, r.usage.output_tokens, r.usage.cache_read_tokens) == (10, 4, 0)


def test_complete_omits_temperature_when_sdk_signature_does_not_support_it():
    class Messages:
        async def create(self, *, model, system, max_tokens, tools, messages):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
            )

    client = AnthropicClient("test-model", client=SimpleNamespace(messages=Messages()))
    result = asyncio.run(
        client.complete(
            system="system",
            messages=(Message(role="user", text="hello"),),
            tools=(),
            temperature=0,
            max_tokens=16,
        )
    )

    assert result.text == "ok"
