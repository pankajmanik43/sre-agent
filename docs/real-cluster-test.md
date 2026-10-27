Docker is confirmed running (docker info returns a server version). Run the real-cluster test for sre-agent. ANTHROPIC_API_KEY is set in the environment — never print it or write it to a file.

Rules: only run kind, kubectl, docker, and sre-agent commands without asking. Ask before anything else. Stop and tell me if any step fails; don't improvise around it.

1. kind create cluster --name sre-agent. Confirm with kubectl cluster-info --context kind-sre-agent.
2. kubectl create namespace payments. Create deployment checkout-api in it using busybox:1.36 running: sh -c 'echo FATAL: DATABASE_URL not set >&2; exit 1'. Poll kubectl -n payments get pods until STATUS is CrashLoopBackOff (up to 2 minutes).
3. Run: sre-agent examples/incident_pod_crashloop.json --context kind-sre-agent. Print the full JSON output exactly as returned.
4. Save the read-only ClusterRole YAML from README.md to rbac-readonly.yaml and apply it. Create namespace sre-agent and serviceaccount sre-agent in it. Bind with clusterrolebinding sre-agent-readonly. Mint a token with --duration=8h, create credentials sre-agent-ro, and create context ro on cluster kind-sre-agent with that user.
5. Prove it: kubectl --context ro auth can-i delete pods -n payments (expect "no"), kubectl --context ro -n payments get pods (expect the pod). Show both.
6. Run: sre-agent examples/incident_pod_crashloop.json --context ro. Print the full JSON output exactly as returned.
7. Do NOT delete the cluster or the ro context. Finish with a short summary: did both runs succeed, how many tool calls each, any difference between admin and ro runs.

Before starting, save these steps verbatim to docs/real-cluster-test.md so they're in the repo next time.

## Verified

2026-10-27, `ro` context on kind cluster `sre-agent`, namespace `payments`:

| Check | Result |
|---|---|
| `kubectl --context ro auth can-i delete pods -n payments` | no |
| `kubectl --context ro auth can-i get pods -n payments` | yes |
| `kubectl --context ro auth can-i list endpoints -n payments` | no |
| `kubectl --context ro auth can-i get configmaps -n payments` | yes |
| `kubectl --context ro auth can-i get secrets -n payments` | no |

These were run against the earlier manifest, which granted `configmaps` and `namespaces`. Both were
then removed from `deploy/rbac-readonly.yaml`; re-apply it and re-run the checks to confirm
`get configmaps` now returns no.
