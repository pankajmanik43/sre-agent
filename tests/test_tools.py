import pytest

from agent.tools import TOOL_SPECS, ToolContext, registered_tools, run_tool
from tests.conftest import FakeKubectl

TIMEOUT = "--request-timeout=30s"


def test_every_spec_has_a_handler_and_vice_versa():
    assert {spec["name"] for spec in TOOL_SPECS} == set(registered_tools())


@pytest.mark.parametrize("spec", TOOL_SPECS, ids=lambda s: s["name"])
def test_specs_are_well_formed(spec):
    schema = spec["input_schema"]
    assert spec["description"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) <= set(schema["properties"])


@pytest.mark.parametrize(
    ("name", "tool_input", "argv"),
    [
        (
            "get_pods",
            {"namespace": "payments"},
            ["kubectl", "get", "pods", "-n", "payments", "-o", "wide", TIMEOUT],
        ),
        (
            "get_pods",
            {"namespace": "payments", "selector": "app=checkout-api"},
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                "payments",
                "-o",
                "wide",
                "-l",
                "app=checkout-api",
                TIMEOUT,
            ],
        ),
        (
            "describe_resource",
            {"kind": "Deployment", "name": "checkout-api", "namespace": "payments"},
            ["kubectl", "describe", "deployment", "checkout-api", "-n", "payments", TIMEOUT],
        ),
        (
            "describe_resource",
            {"kind": "node", "name": "node-1", "namespace": "ignored"},
            ["kubectl", "describe", "node", "node-1", TIMEOUT],
        ),
        (
            "get_pod_logs",
            {"name": "checkout-api-1", "namespace": "payments"},
            ["kubectl", "logs", "checkout-api-1", "-n", "payments", "--tail=200", TIMEOUT],
        ),
        (
            "get_pod_logs",
            {
                "name": "checkout-api-1",
                "namespace": "payments",
                "container": "app",
                "tail": 50,
                "previous": True,
            },
            [
                "kubectl",
                "logs",
                "checkout-api-1",
                "-n",
                "payments",
                "--tail=50",
                "-c",
                "app",
                "--previous",
                TIMEOUT,
            ],
        ),
        (
            "get_events",
            {"namespace": "payments", "involved_object": "checkout-api-1"},
            [
                "kubectl",
                "get",
                "events",
                "-n",
                "payments",
                "--sort-by=.lastTimestamp",
                "--field-selector=involvedObject.name=checkout-api-1",
                TIMEOUT,
            ],
        ),
        (
            "top_pods",
            {"namespace": "payments"},
            ["kubectl", "top", "pods", "-n", "payments", TIMEOUT],
        ),
        ("top_nodes", {}, ["kubectl", "top", "nodes", TIMEOUT]),
    ],
)
def test_tool_builds_expected_argv(ctx, fake_kubectl, name, tool_input, argv):
    content, is_error = run_tool(name, tool_input, ctx)
    assert not is_error, content
    assert fake_kubectl.calls == [argv]


def test_tool_returns_backend_output():
    ctx = ToolContext(kubectl=FakeKubectl(outputs={"get": "checkout-api-1  0/1  CrashLoopBackOff"}))
    content, is_error = run_tool("get_pods", {"namespace": "payments"}, ctx)
    assert (content, is_error) == ("checkout-api-1  0/1  CrashLoopBackOff", False)


@pytest.mark.parametrize(
    ("name", "tool_input", "message"),
    [
        ("delete_pod", {"name": "x"}, "unknown tool"),
        ("get_pods", {}, "missing required"),
        ("get_pods", {"namespace": "payments", "all_namespaces": True}, "unknown arguments"),
        ("get_pods", {"namespace": "--all-namespaces"}, "invalid namespace"),
        ("get_pods", {"namespace": 3}, "must be of type string"),
        ("get_pod_logs", {"name": "p", "namespace": "n", "tail": True}, "must be of type integer"),
        ("get_pod_logs", {"name": "p", "namespace": "n", "tail": 100_000}, "tail must be"),
        ("describe_resource", {"kind": "secret", "name": "db-creds"}, "not allowed"),
    ],
)
def test_bad_input_returns_error_without_calling_kubectl(
    ctx, fake_kubectl, name, tool_input, message
):
    content, is_error = run_tool(name, tool_input, ctx)
    assert is_error
    assert message in content
    assert fake_kubectl.calls == []


def test_kubectl_failure_reported_as_tool_error():
    ctx = ToolContext(kubectl=FakeKubectl(error="metrics API not available"))
    content, is_error = run_tool("top_nodes", {}, ctx)
    assert is_error
    assert "metrics API not available" in content
