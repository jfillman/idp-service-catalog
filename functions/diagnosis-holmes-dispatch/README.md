# diagnosis-holmes-dispatch

Thin replacement for `ai-rollout`'s bespoke `diagnosis-job/agent.py`. Where
`agent.py` ran its own Claude tool-use loop (spawning `kubernetes-mcp-server`
and `github-mcp-server` as stdio subprocesses, holding its own
`ANTHROPIC_API_KEY` + `GITHUB_PERSONAL_ACCESS_TOKEN`), this script holds
**neither credential** — it makes exactly one HTTP call to an
already-running, shared [HolmesGPT](https://github.com/robusta-dev/holmesgpt)
service (`POST /api/chat`), which has its own standing credentials and its
own k8s + GitHub MCP toolsets already wired up.

Rendered by [`../function-rollout-watcher`](../function-rollout-watcher)'s
`build_diagnosis_job()` as a plain `batch/v1` Job, using the same env-var
contract the original `diagnosis-job` used
(`ROLLOUT_NAME`/`ROLLOUT_NAMESPACE`/`GITOPS_*`/`SRC_*`) — the same
investigation protocol (mono vs. polyrepo branching, the three root-cause
classes, PR format requirements) is ported into `additional_system_prompt`
rather than a hardcoded `SYSTEM_PROMPT` constant, so it travels with the
request instead of living in code.

## Why a separate Job at all, instead of the Composition Function calling
## Holmes directly

A real investigation can take several minutes (HolmesGPT's own agentic tool
loop, live-verified taking ~8 minutes on the `widget-api` demo). Crossplane
Composition Functions need to return quickly within a reconcile — they can't
block on that. The Job still exists for the same reason it always did:
somewhere to run a long-lived, fire-and-forget call. What changed is what's
*inside* the Job: previously a full agent, now a single `requests.post(...)`
plus response handling.

## Required environment variables

Same as the original `diagnosis-job`, set by `function-rollout-watcher`'s
`build_diagnosis_job()` — see that function's `fn.py` for the exact mapping:

```
ROLLOUT_NAME, ROLLOUT_NAMESPACE
GITOPS_OWNER, GITOPS_REPO, GITOPS_BASE_BRANCH, GITOPS_MANIFEST_PATH
SRC_OWNER, SRC_REPO, SRC_BASE_BRANCH, SRC_PATH
```

Optional: `HOLMES_URL` (defaults to the in-cluster HolmesGPT Service DNS
name), `HOLMES_MODEL` (defaults to Holmes' own configured model).

## RBAC

None. The Job's `ServiceAccount` (`diagnosis-dispatch`) carries zero RBAC
grants — it never touches the Kubernetes API directly, only HTTP to Holmes.
This is a real reduction from the original `diagnosis-agent` Role
(pods/logs/events/rollouts read access), not just a style choice: the
investigation work moved to Holmes, which already has its own (broader,
cluster-wide) RBAC.

## Live-verified

2026-08-13, on a fresh `kind-dev` cluster: a real broken canary rollout
(`ai-rollout`'s image-tag break scenario) triggered a real dispatch, Holmes
investigated for real, and opened a real PR —
[jfillman/idp#8](https://github.com/jfillman/idp/pull/8). See
[`../function-rollout-watcher`](../function-rollout-watcher)'s README for
the full result.

## Build and push

Same registry/auth notes as `function-rollout-watcher` apply (plain OCI
image, not an xpkg — this one's just a normal container image referenced
directly in the rendered Job spec):

```shell
docker build -t ghcr.io/jfillman/diagnosis-holmes-dispatch:<tag> .
docker push ghcr.io/jfillman/diagnosis-holmes-dispatch:<tag>
```

Not yet pushed anywhere durable — the `kind-dev` proof used a locally built
image (`kind load docker-image`), referenced as
`localhost/diagnosis-holmes-dispatch:latest` in the Composition's
`diagnosisJobImage` input. Needs a real registry push (matching
`function-rollout-watcher`'s `v0.2.0-holmes-dispatch` tag convention) before
this is usable on any cluster that isn't `kind-dev` itself.
