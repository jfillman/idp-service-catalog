# function-rollout-watcher

Crossplane Composition Function (Python), step 2 of a 3-step pipeline on the
`Application` XRD (`apps.example.org`):

1. `function-go-templating` renders the actual app resources (Argo `Rollout`,
   `Service`, `AnalysisTemplate`) from Go template files.
2. **This function** watches the `Rollout` step 1 rendered. The first time
   it's `Degraded`/`Error` for a revision it hasn't handled yet, it renders a
   diagnosis `Job` (see [`../diagnosis-holmes-dispatch`](../diagnosis-holmes-dispatch))
   and records `status.lastDiagnosisRevision` / `status.lastDiagnosisJob` on
   the XR.
3. `function-auto-ready` marks resources Ready based on their own conditions.

Both the `Rollout` (read here) and the `Job` (rendered here) are plain native
Kubernetes resources — Crossplane v2 composes these directly for a namespaced
XR, no `provider-kubernetes`/`Object` wrapper needed.

## Origin and redesign

Extracted from [`ai-rollout`](https://github.com/jfillman/ai-rollout) — a
standalone prototype (`/Users/jerf/tech/ai-rollout` locally) that proved this
mechanism out end-to-end with a bespoke `diagnosis-job`: a Kubernetes Job
running its own Claude tool-use loop, spawning `kubernetes-mcp-server` and
`github-mcp-server` as stdio subprocesses, holding its own
`ANTHROPIC_API_KEY` and `GITHUB_PERSONAL_ACCESS_TOKEN`. That prototype is
left untouched as a standalone demo, not built on top of in place.

**This copy is redesigned to hand the investigation off to a shared,
already-running [HolmesGPT](https://github.com/robusta-dev/holmesgpt)
service instead of running a bespoke agent per app.** `build_diagnosis_job()`
in `function/fn.py` now renders a much thinner Job (see
[`../diagnosis-holmes-dispatch`](../diagnosis-holmes-dispatch)) that makes a
single HTTP call to Holmes' `/api/chat` and holds **no credentials at all** —
Holmes has its own standing `ANTHROPIC_API_KEY` and GitHub MCP access,
already broader in practice than the bespoke agent's (native k8s toolset with
cluster-wide RBAC vs. a hand-scoped namespaced Role; `repos,issues,
pull_requests,actions,context` GitHub toolset vs. `repos,pull_requests`
only). The `ServiceAccount` the Job runs as (`diagnosis-dispatch`) carries
zero RBAC grants, for the same reason.

**Live-verified end-to-end on a fresh `kind-dev` cluster (2026-08-13)**: a
real broken canary (`ai-rollout`'s `break-demo-via-xr.sh` image-tag
scenario) triggered a real diagnosis Job, which correctly diagnosed the root
cause (including recognizing the bad tag was live cluster drift, never
actually committed to git) and opened a real, well-evidenced fix PR —
[jfillman/idp#8](https://github.com/jfillman/idp/pull/8). It also caught a
second, unrelated, pre-existing drift (a replica-count mismatch) and
correctly flagged it for human review rather than guessing. Cost for that one
investigation: ~$0.97, mostly cached tokens.

## What's still the same as the original

- Reusable across multiple Applications/GitOps repos: `ROLLOUT_NAME`/
  `ROLLOUT_NAMESPACE` and the GitOps/source repo coordinates are always
  derived from whichever XR is being reconciled — never hardcoded. Per-XR
  annotation overrides (`gitops.example.org/*`, `src.example.org/*`) still
  work exactly as before; see the annotations on
  [`demo-app/application.yaml`](https://github.com/jfillman/ai-rollout/blob/main/demo-app/application.yaml)
  in the original prototype for the full annotation list.
- Same 3-step pipeline shape, same native-resource composition approach.

## What's genuinely still the single-repo demo shape (Phase 2 TODO)

This function itself is already generic (see above), but the Composition/XRD
it's paired with today is still the one proven on the `widget-api` demo, not
yet generalized into the real `idp-application` chart /
`ApplicationEnvironment` XRD design described in
[`idp`'s `docs/service-catalog-design.md`](https://github.com/jfillman/idp/blob/main/docs/service-catalog-design.md).
That's the next piece of work, not done here.

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
crossplane xpkg push \
  --package-files=function-rollout-watcher.xpkg \
  ghcr.io/jfillman/function-rollout-watcher:<tag>
```

Current known-good tag: `ghcr.io/jfillman/function-rollout-watcher:v0.2.0-holmes-dispatch`
(private package — needs a `packagePullSecrets` entry on the `Function`
resource pointing at a `kubernetes.io/dockerconfigjson` Secret, same pattern
`platform-cicd` already uses for its `registry-credentials` Secret).

## Known gaps

- **Test tooling is broken, independent of the redesign above**: the
  `.venv-default` copied in from the scaffold has hardcoded shebangs
  pointing at a now-nonexistent path from an earlier project layout (venvs
  aren't relocatable), and this repo's `pyproject.toml` pins `<3.14` while
  the only local Python available when this was last touched was 3.14. No
  hatch env named `test` is actually configured despite occasional
  documentation elsewhere implying `hatch run test:unit` works. Needs a
  compatible Python (3.11-3.13) and a fresh venv, or a proper
  `[tool.hatch.envs.hatch-test]` config, before unit tests can run again.
  The Holmes-dispatch change itself was validated by real end-to-end
  execution instead (see above), not by unit tests.
- `example/` and `tests/test_fn.py` still reflect the scaffold/original
  prototype's fixtures and haven't been reviewed against this redesign.
