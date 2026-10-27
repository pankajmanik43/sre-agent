from __future__ import annotations

from typing import Any

import pytest
from anthropic.types.beta import BetaMessage, BetaTextBlock, BetaToolUseBlock

from agent.kubectl import KubectlError, KubectlRunner
from agent.tools import ToolContext


class FakeKubectl(KubectlRunner):
    """Records the argv each call would run and returns canned output instead of executing."""

    def __init__(self, outputs: dict[str, str] | None = None, error: str | None = None) -> None:
        super().__init__(binary="kubectl")
        self.outputs = outputs or {}
        self.error = error
        self.calls: list[list[str]] = []

    def run(self, verb: str, *args: str) -> str:
        argv = self.build_argv(verb, *args)
        self.calls.append(argv)
        if self.error:
            raise KubectlError(self.error)
        return self.outputs.get(verb, f"output of kubectl {verb}")


class _FakeMessages:
    def __init__(self, responses: list[BetaMessage]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> BetaMessage:
        # Snapshot messages: the agent keeps appending to the same list.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if not self._responses:
            raise AssertionError("fake Anthropic client ran out of scripted responses")
        return self._responses.pop(0)


class FakeAnthropic:
    """Stand-in for anthropic.Anthropic exposing only client.beta.messages.create."""

    def __init__(self, responses: list[BetaMessage]) -> None:
        self.beta = type("Beta", (), {})()
        self.beta.messages = _FakeMessages(responses)


def tool_use(name: str, tool_input: dict[str, Any], block_id: str) -> BetaToolUseBlock:
    return BetaToolUseBlock.model_construct(
        type="tool_use", id=block_id, name=name, input=tool_input
    )


def text(value: str) -> BetaTextBlock:
    return BetaTextBlock.model_construct(type="text", text=value, citations=None)


def message(*content: Any, stop_reason: str = "tool_use") -> BetaMessage:
    return BetaMessage.model_construct(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-opus-5-5",
        content=list(content),
        stop_reason=stop_reason,
        stop_sequence=None,
    )


VALID_ASSESSMENT: dict[str, Any] = {
    "summary": "checkout-api is crash looping after a config change removed DATABASE_URL.",
    "severity": "SEV2",
    "affected_resources": [
        {"kind": "Pod", "name": "checkout-api-7d9f8c6b5-x2kq9", "namespace": "payments"}
    ],
    "verified_facts": ["Pod restarted 12 times", "Previous logs show 'DATABASE_URL not set'"],
    "assumptions": ["The ConfigMap change at 09:10 was the only recent deploy"],
    "hypotheses": [
        {
            "cause": "Missing DATABASE_URL environment variable",
            "confidence": 0.8,
            "evidence": ["KeyError: DATABASE_URL in previous container logs"],
            "diagnostic_commands": [
                "kubectl get deployment checkout-api -n payments -o yaml",
            ],
        },
        {
            "cause": "Database unreachable",
            "confidence": 0.1,
            "evidence": ["No connection errors seen"],
            "diagnostic_commands": ["kubectl logs checkout-api-7d9f8c6b5-x2kq9 -n payments"],
        },
    ],
    "safe_next_actions": ["Compare the current ConfigMap with the previous revision"],
    "rollback_plan": "kubectl rollout undo deployment/checkout-api -n payments",
}


@pytest.fixture
def fake_kubectl() -> FakeKubectl:
    return FakeKubectl()


@pytest.fixture
def ctx(fake_kubectl: FakeKubectl) -> ToolContext:
    return ToolContext(kubectl=fake_kubectl)
