import subprocess
from unittest import mock

import pytest

from agent.kubectl import (
    ALLOWED_VERBS,
    KubectlError,
    KubectlNotAllowed,
    KubectlRunner,
    validate_name,
    validate_selector,
)

MUTATING_VERBS = [
    "apply",
    "create",
    "delete",
    "edit",
    "patch",
    "replace",
    "scale",
    "rollout",
    "exec",
    "cp",
    "port-forward",
    "drain",
    "cordon",
    "taint",
    "label",
    "annotate",
    "set",
    "debug",
]


def test_allowed_verbs_are_read_only():
    assert ALLOWED_VERBS == {"get", "describe", "logs", "top"}


@pytest.mark.parametrize("verb", MUTATING_VERBS)
def test_mutating_verbs_rejected_before_subprocess(verb):
    with mock.patch("agent.kubectl.subprocess.run") as run:
        with pytest.raises(KubectlNotAllowed):
            KubectlRunner().run(verb, "pods")
    run.assert_not_called()


def test_run_uses_argv_without_shell():
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=b"pod-a Running", stderr=b""
    )
    with mock.patch("agent.kubectl.subprocess.run", return_value=completed) as run:
        out = KubectlRunner(context="prod", timeout=10).run("get", "pods", "-n", "payments")

    assert out == "pod-a Running"
    argv = run.call_args.args[0]
    assert argv == [
        "kubectl",
        "get",
        "pods",
        "-n",
        "payments",
        "--request-timeout=10s",
        "--context=prod",
    ]
    assert run.call_args.kwargs.get("shell", False) is False


def test_nonzero_exit_raises_with_stderr():
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=b"", stderr=b'pods "x" not found'
    )
    with mock.patch("agent.kubectl.subprocess.run", return_value=completed):
        with pytest.raises(KubectlError, match="not found"):
            KubectlRunner().run("get", "pods", "x")


def test_missing_binary_raises():
    with mock.patch("agent.kubectl.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(KubectlError, match="not found"):
            KubectlRunner().run("get", "pods")


def test_timeout_raises():
    with mock.patch(
        "agent.kubectl.subprocess.run", side_effect=subprocess.TimeoutExpired("kubectl", 1)
    ):
        with pytest.raises(KubectlError, match="timed out"):
            KubectlRunner().run("get", "pods")


def test_output_truncated_keeping_tail():
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=b"a" * 50 + b"END", stderr=b""
    )
    with mock.patch("agent.kubectl.subprocess.run", return_value=completed):
        out = KubectlRunner(max_output_bytes=10).run("logs", "pod")
    assert out.startswith("[truncated")
    assert out.endswith("END")


@pytest.mark.parametrize(
    "value",
    ["--all-namespaces", "-A", "foo;rm -rf /", "Foo", "a b", "", "x" * 300, "../etc"],
)
def test_validate_name_rejects_unsafe_values(value):
    with pytest.raises(KubectlNotAllowed):
        validate_name(value)


@pytest.mark.parametrize("value", ["payments", "checkout-api-7d9f8c6b5-x2kq9", "node-1.internal"])
def test_validate_name_accepts_k8s_names(value):
    assert validate_name(value) == value


@pytest.mark.parametrize("value", ["app=api", "app in (a,b),tier!=db", "app.kubernetes.io/name=x"])
def test_validate_selector_accepts_label_selectors(value):
    assert validate_selector(value) == value


@pytest.mark.parametrize("value", ["--kubeconfig=/tmp/x", "-A", "app=api;ls", ""])
def test_validate_selector_rejects_flags(value):
    with pytest.raises(KubectlNotAllowed):
        validate_selector(value)
