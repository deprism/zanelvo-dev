"""Deterministic tests for AnthropicProvider's prompt caching — the "aggressive caching to save on
LLM tokens" fix. No network: client.messages.create is replaced with a fake that captures exactly
what was sent, so these assert the real request shape Anthropic's caching API requires, not just
that some code ran.
"""
import asyncio
import copy
from types import SimpleNamespace

from app.devstudio.providers.anthropic_provider import (
    _mark_last_message_cacheable,
    _strip_cache_control,
    AnthropicProvider,
)


class _FakeBlock(SimpleNamespace):
    def model_dump(self):
        return dict(self.__dict__)


def _fake_resp(text="ok"):
    block = _FakeBlock(type="text", text=text)
    return SimpleNamespace(content=[block], usage=SimpleNamespace(
        input_tokens=10, output_tokens=2, cache_read_input_tokens=0, cache_creation_input_tokens=0))


def _provider_with_fake_client(captured, resp=None):
    provider = AnthropicProvider(api_key="sk-ant-fake")

    class FakeMessages:
        async def create(self, **kwargs):
            # Snapshot now — the caller mutates its own `messages` list (appends the assistant
            # turn) right after this returns, and that's the SAME list object we'd otherwise hold
            # a reference to.
            captured.update({k: (copy.deepcopy(v) if k == "messages" else v) for k, v in kwargs.items()})
            return resp or _fake_resp()

    class FakeClient:
        messages = FakeMessages()

    provider._client = lambda: FakeClient()
    return provider


def test_generate_marks_the_system_prompt_as_a_cache_breakpoint():
    captured = {}
    provider = _provider_with_fake_client(captured)
    asyncio.run(provider.generate(system="You are a test agent.", prompt="hi", model="claude-sonnet-5"))
    assert captured["system"] == [
        {"type": "text", "text": "You are a test agent.", "cache_control": {"type": "ephemeral"}}
    ]


def test_generate_structured_also_caches_the_extended_system_prompt():
    captured = {}
    provider = _provider_with_fake_client(captured)
    asyncio.run(provider.generate_structured(system="Base prompt.", prompt="hi", model="claude-sonnet-5"))
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Base prompt." in captured["system"][0]["text"]
    assert "JSON" in captured["system"][0]["text"]


def test_generate_with_tools_caches_system_and_the_last_tool_definition():
    captured = {}
    provider = _provider_with_fake_client(captured)
    tools = [
        {"name": "view_file", "description": "read a file", "inputSchema": {"type": "object"}},
        {"name": "execute_bash", "description": "run a command", "inputSchema": {"type": "object"}},
    ]
    asyncio.run(provider.generate_with_tools(system="sys", model="claude-sonnet-5", tools=tools, prompt="go"))
    assert captured["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert "cache_control" not in captured["tools"][0]
    assert captured["tools"][1]["cache_control"] == {"type": "ephemeral"}


def test_generate_with_tools_marks_the_last_message_cacheable():
    captured = {}
    provider = _provider_with_fake_client(captured)
    asyncio.run(provider.generate_with_tools(system="sys", model="claude-sonnet-5", tools=[], prompt="go"))
    last = captured["messages"][-1]
    assert last["content"] == [{"type": "text", "text": "go", "cache_control": {"type": "ephemeral"}}]


def test_generate_with_tools_marks_tool_results_message_cacheable_not_the_string_prompt():
    captured = {}
    provider = _provider_with_fake_client(captured)
    history = [{"role": "user", "content": "go"}, {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]
    asyncio.run(provider.generate_with_tools(
        system="sys", model="claude-sonnet-5", tools=[], history=history,
        tool_results=[{"id": "t1", "content": "42"}]))
    last = captured["messages"][-1]
    assert last["role"] == "user"
    assert last["content"][-1]["type"] == "tool_result"
    assert last["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert last["content"][-1]["tool_use_id"] == "t1"


def test_only_one_cache_breakpoint_is_kept_active_across_iterations():
    # Regression guard for Anthropic's 4-breakpoints-per-request cap: a long tool loop must not
    # accumulate a stale cache_control on every previous turn's last message.
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "first", "cache_control": {"type": "ephemeral"}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
    ]
    _strip_cache_control(messages)
    _mark_last_message_cacheable(messages)
    marked = [
        block for m in messages for block in (m["content"] if isinstance(m["content"], list) else [])
        if isinstance(block, dict) and "cache_control" in block
    ]
    assert len(marked) == 1
    assert messages[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_vertex_claude_adapter_inherits_the_same_caching_for_free():
    # GeminiEnterpriseProvider's Claude dispatch (_VertexClaudeAdapter) subclasses AnthropicProvider
    # and only overrides _client() — it must pick up the exact same caching behavior, not a
    # reimplementation that could drift. Anthropic's own docs list prompt caching as a supported
    # feature on Agent Platform too, so this isn't a no-op there.
    from app.devstudio.providers.gemini_enterprise_provider import _VertexClaudeAdapter

    captured = {}
    adapter = _VertexClaudeAdapter(lambda: None)

    class FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _fake_resp()

    class FakeClient:
        messages = FakeMessages()

    adapter._client = lambda: FakeClient()
    asyncio.run(adapter.generate(system="sys", prompt="hi", model="claude-sonnet-5"))
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
