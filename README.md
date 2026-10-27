# sre-agent

A Kubernetes incident triage agent. Give it a description of the problem or an Alertmanager-style JSON
payload. Claude investigates with **read-only** kubectl calls and returns a structured
`IncidentAssessment` (see `agent/schemas.py`) containing:

- verified facts
- assumptions
- ranked hypotheses, each with read-only diagnostic commands
- safe next actions
- an optional rollback plan

## Read-only guarantee

- `agent/kubectl.py` runs only `get`, `describe`, `logs` and `top`. Any other verb raises before
  a process is started.
- kubectl is run with an argv list, never through a shell. Names, namespaces and label selectors that come from
  the model are validated, so they can't inject flags like `--kubeconfig` or `-A`.
- `describe` is limited to an allowlist of kinds. Secrets and ConfigMaps aren't on it.
- `diagnostic_commands` in the assessment are validated to be read-only kubectl invocations.

Defence in depth: run the agent with a kubeconfig bound to a read-only role (below), so the
cluster refuses writes even if the code were changed.

## Setup

Requires Python 3.12+ and `kubectl` on `PATH`.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...  # Windows PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."
```

### Read-only RBAC (recommended)

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: sre-agent-readonly
rules:
  - apiGroups: [""]
    resources: [pods, pods/log, events, services, endpoints, nodes, persistentvolumeclaims]
    verbs: [get, list, watch]
  - apiGroups: [apps]
    resources: [deployments, replicasets, statefulsets, daemonsets]
    verbs: [get, list, watch]
  - apiGroups: [batch]
    resources: [jobs, cronjobs]
    verbs: [get, list, watch]
  - apiGroups: [networking.k8s.io]
    resources: [ingresses]
    verbs: [get, list, watch]
  - apiGroups: [autoscaling]
    resources: [horizontalpodautoscalers]
    verbs: [get, list, watch]
  - apiGroups: [metrics.k8s.io]
    resources: [pods, nodes]
    verbs: [get, list]
```

Bind it to a ServiceAccount and build a kubeconfig context for that account. Then pass the context with
`--context`.

## Usage

```bash
# Free-text description
sre-agent "checkout-api pods in payments keep restarting"

# JSON alert payload
sre-agent examples/incident_pod_crashloop.json --context staging-readonly

# Options
sre-agent --help
#   --model      default claude-opus-5-5
#   --effort     low|medium|high|xhigh|max (default high)
#   --context    kubeconfig context
#   --max-turns  tool-use turns before giving up (default 20)
```

The assessment JSON goes to stdout and errors go to stderr. Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Agent or API failure |
| 2 | Bad input |

The agent calls the Claude API with adaptive thinking and server-side refusal fallback
(`fallbacks="default"`). If a safety classifier declines a request, it's retried on Anthropic's
recommended fallback model rather than aborting the investigation.

## Development

```bash
ruff check . && ruff format --check . && pytest
```

The tests never touch the network or a cluster. The Anthropic client is replaced by a scripted
fake (`tests/conftest.py::FakeAnthropic`), and kubectl by a recording runner (`FakeKubectl`) or a
patched `subprocess.run`.

### Adding a tool

Tools live in `agent/tools.py`:

1. Write `def my_tool(ctx: ToolContext, ...) -> str` and decorate it with `@register`.
2. Add its entry to `TOOL_SPECS` (name, description, `input_schema` with
   `additionalProperties: false`).
3. Add a test in `tests/test_tools.py` that uses a mocked backend.

If the tool needs a new backend, such as a Prometheus client, add it as a field on `ToolContext`.
A test fails if `TOOL_SPECS` and the registered handlers fall out of sync.

## CI (`.gitlab-ci.yml`)

| Stage | Job | What it does |
|---|---|---|
| lint | `ruff` | `ruff check` and `ruff format --check` |
| test | `pytest` | Tests with JUnit and Cobertura coverage reports; freezes installed deps for Trivy |
| security | `trivy` | `trivy fs` vuln + secret + misconfig scan; fails on fixable HIGH/CRITICAL; JSON report artifact |

The Trivy image is pinned to a fixed tag. Pin it by digest and update it deliberately rather than
tracking `latest`.
