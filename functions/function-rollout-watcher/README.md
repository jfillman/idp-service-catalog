# function-rollout-watcher

Crossplane Composition Function (Python). Second redesign — see "Origin and
redesign history" below. Today it's the sole pipeline step on the
`RolloutWatch` XRD (`catalog.idp.io`, see
[`../../xrds/rolloutwatch.yaml`](../../xrds/rolloutwatch.yaml) /
[`../../compositions/rolloutwatch`](../../compositions/rolloutwatch)):

1. Requests the live, Helm-created Argo `Rollout` as a Crossplane
   extra-resources requirement (matched by name + namespace — same name as
   this XR, same namespace, `idp-application`'s own naming convention) and
   reads its *observed* status from there.
2. The first time it's `Degraded`/`Error` for a revision it hasn't handled
   yet, composes a diagnosis `Job` (see
   [`../diagnosis-holmes-dispatch`](../diagnosis-holmes-dispatch)) and
   records `status.lastDiagnosisRevision` / `status.lastDiagnosisJob` on the
   XR.

The Rollout is never composed here, only read. The Job (composed here) is a
plain native Kubernetes resource — Crossplane v2 composes it directly for a
namespaced XR, no `provider-kubernetes`/`Object` wrapper needed.

## Origin and redesign history

Extracted from [`ai-rollout`](https://github.com/jfillman/ai-rollout) — a
standalone prototype (`/Users/jerf/tech/ai-rollout` locally) that proved this
mechanism out end-to-end with a bespoke `diagnosis-job`: a Kubernetes Job
running its own Claude tool-use loop, spawning `kubernetes-mcp-server` and
`github-mcp-server` as stdio subprocesses, holding its own
`ANTHROPIC_API_KEY` and `GITHUB_PERSONAL_ACCESS_TOKEN`. That prototype is
left untouched as a standalone demo, not built on top of in place.

**Redesign 1 (2026-08-13): hand the investigation off to
[HolmesGPT](https://github.com/robusta-dev/holmesgpt).** `build_diagnosis_job()`
in `function/fn.py` renders a much thinner Job (see
[`../diagnosis-holmes-dispatch`](../diagnosis-holmes-dispatch)) that makes a
single HTTP call to Holmes' `/api/chat` and holds **no credentials at all** —
Holmes has its own standing `ANTHROPIC_API_KEY` and GitHub MCP access,
broader in practice than the bespoke agent's. The `ServiceAccount` the Job
runs as (`diagnosis-dispatch`) carries zero RBAC grants, for the same reason.
Live-verified end-to-end on a throwaway `kind-dev`: a real broken canary
triggered a real diagnosis Job, correctly diagnosed the root cause (including
recognizing a bad tag was live cluster drift, never committed to git), and
opened a real fix PR — [jfillman/idp#8](https://github.com/jfillman/idp/pull/8).
It also caught a second, unrelated, pre-existing drift and correctly flagged
it for human review rather than guessing.

**Redesign 2 (2026-08-18/19): extra-resources instead of composing the
Rollout.** Redesign 1 still watched `req.observed.resources["rollout"]` — a
Rollout composed by *step 1 of this function's own Composition pipeline*
(`function-go-templating`, rendering the `Application` XRD's Rollout/Service/
AnalysisTemplate directly, ported from `ai-rollout`). But the real deployment
path that got built since — the `idp-application` Helm chart, deployed per
cluster by ArgoCD — never gives Crossplane a hand in creating the Rollout at
all. There was nothing left for that mechanism to attach to (a known gap this
README itself used to flag as "Phase 2 TODO").

Fixed by switching to a real Crossplane extra-resources requirement
(`response.require_resources`/`request.get_required_resource` — the modern
SDK call, **not** the deprecated `extra_resources` proto field, and not
`function-go-templating`'s YAML-only `ExtraResources` meta-resource, which
only applies to that function's own template language) against the live,
Helm-created Rollout. This function now runs alone in a single-step pipeline
on the new `RolloutWatch` XRD — no `function-go-templating` step at all,
since there's nothing left to template.

GitOps/source repo coordinates changed too: the old per-XR annotation scheme
(`gitops.example.org/*`/`src.example.org/*`, inherited from `ai-rollout`'s
single-repo demo shape) is gone. It was never wired to the real system, and
can't work now anyway — this function runs per cluster (wherever the app it
watches is actually deployed; see `idp/docs/service-catalog-design.md` §0's
tier-locality design), so a live cross-cluster lookup against
`NodeJSApplication` isn't possible even in principle. Instead
`resolve_gitops_config`/`resolve_src_config` in `function/fn.py` derive them
deterministically from `spec.appName`/`cluster`/`env` — `gitops-<appName>` /
`<appName>`, same fixed platform owner, `<cluster>/<env>/values.yaml` —
mirroring exactly what `ApplicationEnvironment`'s own Composition already
computes for the same app.

**Live-verified end-to-end on `kind-prod` (2026-08-18/19)**, against a real
(not manufactured) `checkout-api` `ImagePullBackOff` already sitting
Degraded. HolmesGPT installed per-cluster for the first time (resolving the
"shared vs. per-cluster" question Redesign 1 had left explicitly open) — see
`idp/docs/service-catalog-design.md` §0 for the reasoning and the real
`modelList`/Helm-values gotchas hit getting it running.

## Build and push

```shell
docker build . --platform=linux/amd64 --tag runtime-amd64

crossplane xpkg build \
  --package-root=package \
  --embed-runtime-image=runtime-amd64 \
  --package-file=function-rollout-watcher.xpkg

# crossplane xpkg push reads registry credentials from ~/.docker/config.json
# (docker login first) - NOT from podman's ~/.config/containers/auth.json,
# even if `docker` on your machine is actually routed through a podman
# backend. Hit this live: `docker login` succeeded but wrote to the podman
# location, leaving ~/.docker/config.json stale, causing a confusing DENIED
# on push/pull that had nothing to do with token scope. If push/pull auth
# fails after a successful-looking `docker login`, check
# ~/.docker/config.json's mtime before assuming the token is bad.
#
# If `crossplane xpkg build` fails with "image not known": this machine's
# docker CLI is podman-backed, which tags a locally-built image as
# `localhost/<name>` instead of the `docker.io/library/<name>` crossplane
# expects. `docker tag localhost/runtime-amd64:latest
# docker.io/library/runtime-amd64:latest` first.
crossplane xpkg push \
  --package-files=function-rollout-watcher.xpkg \
  ghcr.io/jfillman/function-rollout-watcher:<tag>
```

Current known-good tag: `ghcr.io/jfillman/function-rollout-watcher:v0.2.3`
(public package — no `packagePullSecrets` needed, confirmed live against
`kind-prod`'s installed `Function` resource; `kind-dev` is still pinned to
the older `v0.1.16`, predating the `diagnosis-dispatch-sa` composed
resource entirely).

**v0.2.3 (2026-08-22)**: fixed the composed `diagnosis-dispatch-sa`
`ServiceAccount` never becoming `Ready`. A plain `ServiceAccount` has no
`status.conditions` for Crossplane's default readiness auto-detection to
find, so `build_diagnosis_service_account`'s composed resource sat in
`Creating` forever — the XR's own `Ready` condition never flipped to `True`
("Unready resources: diagnosis-dispatch-sa"), which surfaced up through
ArgoCD's health check as the owning `Application` stuck in `Progressing`.
Caught live on `kind-prod`, stuck since the resource was first composed on
2026-08-19. Fix: set `ready = fnv1.READY_TRUE` explicitly on that composed
resource in `fn.py`, since it has no status Crossplane can infer readiness
from on its own.

## Known gaps

- **Test tooling is still broken, independent of every redesign above**: the
  `.venv-default` copied in from the scaffold has hardcoded shebangs
  pointing at a now-nonexistent path from an earlier project layout (venvs
  aren't relocatable), and this repo's `pyproject.toml` pins `<3.14` while
  the only local Python available when this was last touched was 3.14. No
  hatch env named `test` is actually configured despite occasional
  documentation elsewhere implying `hatch run test:unit` works. Needs a
  compatible Python (3.11-3.13) and a fresh venv, or a proper
  `[tool.hatch.envs.hatch-test]` config, before unit tests can run again.
  Every redesign so far has been validated by real end-to-end execution
  instead (see above), not by unit tests. A `.dockerignore` was added
  (2026-08-19) so this broken venv at least stops breaking the *Docker*
  build (see below) — it doesn't fix the venv itself.
- Getting the Docker build working again (2026-08-19, unrelated to the
  extra-resources redesign itself) surfaced two real, previously-latent
  bugs: the Dockerfile's unpinned `pip install hatch` picked up a hatch
  release whose own build-environment creation (`hatch/env/virtual.py`'s
  `activate()`, which now routes through an `expose_uv` path) silently fails
  to produce a usable venv on this Dockerfile's Debian 12 base image —
  worked around by building the wheel via `pip wheel .` instead of
  `hatch build` (pip's own PEP517 isolation still uses hatchling, the
  declared build backend, it just doesn't go through hatch's own broken
  environment code to get there). Whether this is a real upstream hatch
  regression or something specific to this image wasn't investigated
  further — the workaround is standalone and doesn't depend on the answer.
- `example/` and `tests/test_fn.py` still reflect the original scaffold's
  fixtures, predating even Redesign 1 — never reviewed against either
  redesign.
