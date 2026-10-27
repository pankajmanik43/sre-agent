"""Read-only kubectl backend.

Only the verbs in ALLOWED_VERBS can be executed. Arguments are passed as an argv list
(never through a shell), and values that come from the model are validated first.
"""

from __future__ import annotations

import re
import subprocess

ALLOWED_VERBS = frozenset({"get", "describe", "logs", "top"})

# RFC 1123 subdomain: used for namespaces, pod/container names and most object names.
_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
# Label selectors: keys, values and the =, ==, !=, in (...), notin (...) operators.
_SELECTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/=!,() ]{0,511}$")


class KubectlError(RuntimeError):
    """kubectl failed, timed out or could not be started."""


class KubectlNotAllowed(KubectlError):
    """The requested invocation is not read-only or has unsafe arguments."""


def validate_name(value: str, field: str = "name") -> str:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise KubectlNotAllowed(f"invalid {field}: {value!r}")
    return value


def validate_selector(value: str) -> str:
    if not isinstance(value, str) or not _SELECTOR_RE.fullmatch(value):
        raise KubectlNotAllowed(f"invalid label selector: {value!r}")
    return value


class KubectlRunner:
    def __init__(
        self,
        binary: str = "kubectl",
        context: str | None = None,
        timeout: float = 30.0,
        max_output_bytes: int = 64_000,
    ) -> None:
        self.binary = binary
        self.context = context
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes

    def build_argv(self, verb: str, *args: str) -> list[str]:
        if verb not in ALLOWED_VERBS:
            raise KubectlNotAllowed(f"kubectl verb {verb!r} is not allowed (read-only agent)")
        argv = [self.binary, verb, *args, f"--request-timeout={int(self.timeout)}s"]
        if self.context:
            argv.append(f"--context={self.context}")
        return argv

    def run(self, verb: str, *args: str) -> str:
        argv = self.build_argv(verb, *args)
        try:
            # argv list, no shell, verb checked against ALLOWED_VERBS above.
            proc = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                timeout=self.timeout + 5,
                check=False,
            )
        except FileNotFoundError as exc:
            raise KubectlError(f"kubectl binary not found: {self.binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise KubectlError(f"kubectl {verb} timed out after {self.timeout}s") from exc

        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise KubectlError(f"kubectl {verb} exited {proc.returncode}: {stderr}")
        return self._truncate(proc.stdout)

    def _truncate(self, data: bytes) -> str:
        if len(data) <= self.max_output_bytes:
            return data.decode(errors="replace")
        kept = data[-self.max_output_bytes :].decode(errors="replace")
        return f"[truncated: showing last {self.max_output_bytes} of {len(data)} bytes]\n{kept}"
