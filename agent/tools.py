"""Tools the agent can call.

Adding a tool: write a function taking (ctx, **inputs) -> str, decorate it with @register,
add its entry to TOOL_SPECS, and add a test with a mocked backend. New backends (e.g. a
Prometheus client) go on ToolContext.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.kubectl import KubectlError, KubectlRunner, validate_name, validate_selector


@dataclass
class ToolContext:
    kubectl: KubectlRunner


ToolHandler = Callable[..., str]
_HANDLERS: dict[str, ToolHandler] = {}


def register(fn: ToolHandler) -> ToolHandler:
    _HANDLERS[fn.__name__] = fn
    return fn


def registered_tools() -> dict[str, ToolHandler]:
    return dict(_HANDLERS)


DESCRIBE_KINDS = frozenset(
    {
        "pod",
        "deployment",
        "replicaset",
        "statefulset",
        "daemonset",
        "job",
        "cronjob",
        "service",
        "endpoints",
        "ingress",
        "node",
        "persistentvolumeclaim",
        "horizontalpodautoscaler",
    }
)
MAX_LOG_LINES = 2000


@register
def get_pods(ctx: ToolContext, namespace: str, selector: str | None = None) -> str:
    args = ["pods", "-n", validate_name(namespace, "namespace"), "-o", "wide"]
    if selector:
        args += ["-l", validate_selector(selector)]
    return ctx.kubectl.run("get", *args)


@register
def describe_resource(ctx: ToolContext, kind: str, name: str, namespace: str | None = None) -> str:
    kind = kind.lower()
    if kind not in DESCRIBE_KINDS:
        raise ValueError(f"kind {kind!r} not allowed; choose from {sorted(DESCRIBE_KINDS)}")
    args = [kind, validate_name(name)]
    if namespace and kind != "node":
        args += ["-n", validate_name(namespace, "namespace")]
    return ctx.kubectl.run("describe", *args)


@register
def get_pod_logs(
    ctx: ToolContext,
    name: str,
    namespace: str,
    container: str | None = None,
    tail: int = 200,
    previous: bool = False,
) -> str:
    if not 1 <= tail <= MAX_LOG_LINES:
        raise ValueError(f"tail must be between 1 and {MAX_LOG_LINES}")
    args = [validate_name(name), "-n", validate_name(namespace, "namespace"), f"--tail={tail}"]
    if container:
        args += ["-c", validate_name(container, "container")]
    if previous:
        args.append("--previous")
    return ctx.kubectl.run("logs", *args)


@register
def get_events(ctx: ToolContext, namespace: str, involved_object: str | None = None) -> str:
    args = ["events", "-n", validate_name(namespace, "namespace"), "--sort-by=.lastTimestamp"]
    if involved_object:
        args.append(f"--field-selector=involvedObject.name={validate_name(involved_object)}")
    return ctx.kubectl.run("get", *args)


@register
def top_pods(ctx: ToolContext, namespace: str) -> str:
    return ctx.kubectl.run("top", "pods", "-n", validate_name(namespace, "namespace"))


@register
def top_nodes(ctx: ToolContext) -> str:
    return ctx.kubectl.run("top", "nodes")


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_NAMESPACE = {"type": "string", "description": "Kubernetes namespace."}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_pods",
        "description": "List pods in a namespace with status, restarts, node and IP "
        "(kubectl get pods -o wide).",
        "input_schema": _schema(
            {
                "namespace": _NAMESPACE,
                "selector": {"type": "string", "description": "Label selector, e.g. app=api."},
            },
            ["namespace"],
        ),
    },
    {
        "name": "describe_resource",
        "description": "Describe a resource, including conditions and recent events "
        f"(kubectl describe). Allowed kinds: {', '.join(sorted(DESCRIBE_KINDS))}.",
        "input_schema": _schema(
            {
                "kind": {"type": "string", "enum": sorted(DESCRIBE_KINDS)},
                "name": {"type": "string"},
                "namespace": {**_NAMESPACE, "description": "Omit for nodes."},
            },
            ["kind", "name"],
        ),
    },
    {
        "name": "get_pod_logs",
        "description": "Fetch recent container logs (kubectl logs). Set previous=true to read "
        "the last terminated container, which is what you want for CrashLoopBackOff.",
        "input_schema": _schema(
            {
                "name": {"type": "string", "description": "Pod name."},
                "namespace": _NAMESPACE,
                "container": {
                    "type": "string",
                    "description": "Required for multi-container pods.",
                },
                "tail": {"type": "integer", "minimum": 1, "maximum": MAX_LOG_LINES},
                "previous": {"type": "boolean"},
            },
            ["name", "namespace"],
        ),
    },
    {
        "name": "get_events",
        "description": "List events in a namespace, oldest first, optionally for one object.",
        "input_schema": _schema(
            {
                "namespace": _NAMESPACE,
                "involved_object": {"type": "string", "description": "Object name to filter on."},
            },
            ["namespace"],
        ),
    },
    {
        "name": "top_pods",
        "description": "CPU and memory usage per pod (needs metrics-server).",
        "input_schema": _schema({"namespace": _NAMESPACE}, ["namespace"]),
    },
    {
        "name": "top_nodes",
        "description": "CPU and memory usage per node (needs metrics-server).",
        "input_schema": _schema({}, []),
    },
]

_SPECS_BY_NAME = {spec["name"]: spec for spec in TOOL_SPECS}
_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "number": (int, float),
}


def _check_input(spec: dict[str, Any], tool_input: dict[str, Any]) -> None:
    schema = spec["input_schema"]
    properties = schema["properties"]
    unknown = set(tool_input) - set(properties)
    if unknown:
        raise ValueError(f"unknown arguments: {sorted(unknown)}")
    missing = [key for key in schema["required"] if key not in tool_input]
    if missing:
        raise ValueError(f"missing required arguments: {missing}")
    for key, value in tool_input.items():
        expected = _JSON_TYPES[properties[key]["type"]]
        # bool is a subclass of int; don't accept true/false where an integer is expected.
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ValueError(f"argument {key!r} must be of type {properties[key]['type']}")


def run_tool(name: str, tool_input: dict[str, Any], ctx: ToolContext) -> tuple[str, bool]:
    """Run a tool and return (content, is_error). Errors are reported back to the model."""
    spec = _SPECS_BY_NAME.get(name)
    handler = _HANDLERS.get(name)
    if spec is None or handler is None:
        return f"unknown tool: {name}", True
    try:
        _check_input(spec, tool_input)
        return handler(ctx, **tool_input), False
    except (ValueError, KubectlError) as exc:
        return f"{type(exc).__name__}: {exc}", True
