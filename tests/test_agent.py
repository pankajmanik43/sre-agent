import copy
import json

import anthropic
import httpx2
import pytest
from anthropic.types import RefusalStopDetails

from agent.agent import SUBMIT_TOOL, AgentError, run_agent
from agent.schemas import IncidentAssessment
from tests.conftest import VALID_ASSESSMENT, FakeAnthropic, message, text, tool_use


def _invalid_assessment():
    data = copy.deepcopy(VALID_ASSESSMENT)
    data["severity"] = "SEV9"
    return data


def test_investigates_then_returns_validated_assessment(ctx, fake_kubectl):
    client = FakeAnthropic(
        [
            message(
                text("Checking the pod."),
                tool_use("get_pods", {"namespace": "payments"}, "t1"),
                tool_use("get_events", {"namespace": "payments"}, "t2"),
            ),
            message(
                tool_use(
                    "get_pod_logs",
                    {"name": "checkout-api-1", "namespace": "payments", "previous": True},
                    "t3",
                )
            ),
            message(tool_use(SUBMIT_TOOL, VALID_ASSESSMENT, "t4")),
        ]
    )

    result = run_agent("checkout-api crash looping", client=client, ctx=ctx)

    assert isinstance(result, IncidentAssessment)
    assert result.severity == "SEV2"
    assert [argv[1] for argv in fake_kubectl.calls] == ["get", "get", "logs"]

    calls = client.messages.calls
    assert len(calls) == 3
    # Parallel tool results go back together in one user message, in order.
    second_turn_results = calls[1]["messages"][-1]
    assert second_turn_results["role"] == "user"
    assert [r["tool_use_id"] for r in second_turn_results["content"]] == ["t1", "t2"]
    assert not any(r["is_error"] for r in second_turn_results["content"])


def test_request_shape(ctx):
    client = FakeAnthropic([message(tool_use(SUBMIT_TOOL, VALID_ASSESSMENT, "t1"))])
    run_agent("x", client=client, ctx=ctx, effort="medium")

    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "medium"}
    for absent in ("fallbacks", "betas", "tool_choice"):
        assert absent not in call
    tool_names = [t["name"] for t in call["tools"]]
    assert SUBMIT_TOOL in tool_names
    assert "get_pods" in tool_names


def test_tool_errors_are_returned_to_model(ctx, fake_kubectl):
    client = FakeAnthropic(
        [
            message(tool_use("delete_resource", {"name": "x"}, "t1")),
            message(tool_use(SUBMIT_TOOL, VALID_ASSESSMENT, "t2")),
        ]
    )
    run_agent("x", client=client, ctx=ctx)

    result = client.messages.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert "unknown tool" in result["content"]
    assert fake_kubectl.calls == []


def test_invalid_assessment_gets_one_retry(ctx):
    client = FakeAnthropic(
        [
            message(tool_use(SUBMIT_TOOL, _invalid_assessment(), "t1")),
            message(tool_use(SUBMIT_TOOL, VALID_ASSESSMENT, "t2")),
        ]
    )
    result = run_agent("x", client=client, ctx=ctx)

    assert result.severity == "SEV2"
    feedback = client.messages.calls[1]["messages"][-1]["content"][0]
    assert feedback["is_error"] is True
    assert "severity" in feedback["content"]


def test_invalid_assessment_after_retry_raises(ctx):
    client = FakeAnthropic(
        [
            message(tool_use(SUBMIT_TOOL, _invalid_assessment(), "t1")),
            message(tool_use(SUBMIT_TOOL, _invalid_assessment(), "t2")),
        ]
    )
    with pytest.raises(AgentError, match="failed validation"):
        run_agent("x", client=client, ctx=ctx)


def test_text_only_reply_is_nudged_to_submit(ctx):
    client = FakeAnthropic(
        [
            message(text("I think it's the config."), stop_reason="end_turn"),
            message(tool_use(SUBMIT_TOOL, VALID_ASSESSMENT, "t1")),
        ]
    )
    run_agent("x", client=client, ctx=ctx)
    nudge = client.messages.calls[1]["messages"][-1]
    assert nudge == {"role": "user", "content": f"Call {SUBMIT_TOOL} with your final assessment."}


def test_max_turns_enforced(ctx):
    client = FakeAnthropic([message(tool_use("top_nodes", {}, f"t{i}")) for i in range(3)])
    with pytest.raises(AgentError, match="after 3 turns"):
        run_agent("x", client=client, ctx=ctx, max_turns=3)


@pytest.mark.parametrize("stop_reason", ["max_tokens", "model_context_window_exceeded"])
def test_truncated_responses_raise(ctx, stop_reason):
    client = FakeAnthropic([message(stop_reason=stop_reason)])
    with pytest.raises(AgentError, match=f"stop_reason={stop_reason}"):
        run_agent("x", client=client, ctx=ctx)


def test_refusal_raises_with_reason_and_is_not_retried(ctx):
    details = RefusalStopDetails.model_construct(
        type="refusal", category="cyber", explanation="Request looks like exploit development."
    )
    client = FakeAnthropic(
        [
            message(stop_reason="refusal", stop_details=details),
            message(tool_use(SUBMIT_TOOL, VALID_ASSESSMENT, "t1")),
        ]
    )
    with pytest.raises(
        AgentError, match="declined.*category=cyber: Request looks like exploit development"
    ):
        run_agent("x", client=client, ctx=ctx)
    assert len(client.messages.calls) == 1


def test_refusal_without_details(ctx):
    client = FakeAnthropic([message(stop_reason="refusal")])
    with pytest.raises(AgentError, match="declined the request: no details provided"):
        run_agent("x", client=client, ctx=ctx)


def test_real_sdk_client_serializes_request(ctx):
    """Run the loop through the real SDK over a mock HTTP transport to check the wire format."""

    replies = [
        {"type": "tool_use", "id": "t1", "name": "get_pods", "input": {"namespace": "payments"}},
        {"type": "tool_use", "id": "t2", "name": SUBMIT_TOOL, "input": VALID_ASSESSMENT},
    ]
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "id": f"msg_{len(requests)}",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [replies[len(requests) - 1]],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 10},
            },
        )

    client = anthropic.Anthropic(
        api_key="unused", http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )
    result = run_agent("x", client=client, ctx=ctx)

    assert result.severity == "SEV2"
    assert all(r.url.path == "/v1/messages" for r in requests)
    assert "anthropic-beta" not in requests[0].headers
    body = json.loads(requests[1].content)
    assert "fallbacks" not in body
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "high"}
    # The assistant turn (SDK objects) is replayed, followed by the tool result.
    assert body["messages"][1]["content"][0]["id"] == "t1"
    assert body["messages"][2]["content"][0]["tool_use_id"] == "t1"
