# idp-service-catalog

Crossplane XRDs, Compositions, Composition Functions, and the
`idp-application` Helm chart for [Dream IDP](https://github.com/jfillman/idp)
— the service catalog a Backstage-driven Crossplane plugin turns into
self-service templates. Design lives in `idp`'s
[`docs/service-catalog-design.md`](https://github.com/jfillman/idp/blob/main/docs/service-catalog-design.md);
this repo is where it's actually built.

## Status

**AI-triage mechanism (Phase 2, first slice) — done, live-verified
2026-08-13.** `functions/function-rollout-watcher` + `functions/diagnosis-holmes-dispatch`:
a Crossplane Composition Function that watches an Argo Rollout and, on
`Degraded`, dispatches a diagnosis request to a shared HolmesGPT service
instead of running a bespoke per-app Claude agent. Extracted and redesigned
from the [`ai-rollout`](https://github.com/jfillman/ai-rollout) prototype
(left untouched as a standalone demo). Proven end-to-end on a fresh
`kind-dev` cluster: a real broken canary → real diagnosis → real fix PR,
[jfillman/idp#8](https://github.com/jfillman/idp/pull/8). See each
function's own README for the full detail.

**`idp-application` Helm chart — built, `helm lint`/`helm template` verified
against fixture values (minimal, full-featured incl. blueGreen, and an
`appType: infra` standalone-component release).** Renders §3's full schema
(Argo Rollout, Service, ConfigMaps, ExternalSecret, PVCs, HPA,
PodDisruptionBudget, NetworkPolicy, AnalysisTemplates, `components:`/`slos:` as
generic Crossplane XRs) plus a deliberate v1 resource-coverage pass beyond §3
entirely: a dedicated ServiceAccount (+ imagePullSecrets), `jobs:`/`cronJobs:`
batch tasks (sharing the main workload's env/secrets/config automatically), a
ServiceMonitor (kube-prometheus-stack is already installed cluster-side), and
a raw `extraManifests:` escape hatch. See `charts/idp-application/README.md`
for the concrete decisions made in both passes — including two real bugs
`helm lint`/`helm template` caught live: an `envName`/`env` naming collision,
and Sprig's `default` silently discarding explicit `false`/`0` values (fixed
with `hasKey`-based checks) — and the placeholders still pending confirmation
(ingress-controller namespace selector, attached-resource API group,
ServiceMonitor selector label, who provisions the image-pull Secret).

**Not started yet**: the v1 XRD catalog itself (`NodeJSApplication`,
`SpringBootApplication`, `ApplicationEnvironment`, `SLO`, and the Component
XRDs) and their Compositions — `idp-application` is what a Composition will
render into `gitops-<app-name>`, but nothing calls it yet. Also not built:
the `ClusterAnalysisTemplate` golden-path library and `argocd-cm` `Rollout`
health-check config (§3 says these belong in `idp-cluster-baseline`), and a
real platform default canary step sequence (§3 "Still open" item 3 — the
chart ships a deliberately inert placeholder in the meantime, see its README).
Nothing here has been live-verified against a real cluster/Argo Rollouts
controller/`ApplicationEnvironment` XR yet — fixture-only so far.

## Layout (so far)

```
functions/
  function-rollout-watcher/    Composition Function: watches Rollout, dispatches diagnosis
  diagnosis-holmes-dispatch/   Thin Job: hands the investigation off to HolmesGPT
charts/
  idp-application/             §3's Embedded+Attached tier chart - one release per (app, cluster, env)
```

`xrds/` and `compositions/` don't exist yet — next up.
