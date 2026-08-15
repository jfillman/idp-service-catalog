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

**`idp-application` Helm chart — built, and as of 2026-08-13 live-verified
end-to-end on a real cluster, not just `helm lint`/`helm template`.** Renders
§3's full schema (Argo Rollout, Service, ConfigMaps, ExternalSecret, PVCs, HPA,
PodDisruptionBudget, NetworkPolicy, AnalysisTemplates, `components:`/`slos:` as
generic Crossplane XRs) plus real resource-coverage/simplification passes
beyond §3: ServiceAccount, `jobs:`/`cronJobs:`, ServiceMonitor,
`extraManifests:`, configMap/secret consumption modes, simplified NetworkPolicy
rules. Live-verification pass: `kind-dev` rebuilt from scratch under real
GitOps management (see `gitops-cluster-dev`), `widget-api` migrated onto this
chart for real off the standalone `ai-rollout` prototype's own Composition,
full test pass against real cluster state (NetworkPolicy enforcement,
ServiceMonitor scraping, a real canary rollout with a Prometheus-backed
AnalysisRun, checksum-triggered revisions) - found and fixed two real bugs
(`rollout.canaryAnalysis` didn't exist at all; `analysisTemplates:` silently
dropped `args:`). See `charts/idp-application/README.md` for the full detail
of every pass, including the earlier fixture-only bugs (an `envName`/`env`
naming collision, Sprig's `default` silently discarding explicit `false`/`0`).
The ingress-controller namespace selector is resolved too now (Contour,
confirmed live) - remaining open items: attached-resource API group, who
provisions the image-pull Secret.

**`NodeJSApplication` XRD + Composition — first Bootstrap-tier XRD, built and
live-verified on `kind-dev` 2026-08-13/14.** Item 1/2's design: given an app name,
pure `provider-github` commits a src repo + Node.js boilerplate (Dockerfile,
`package.json`, `index.js`, README — `npm`/`pnpm`/`yarn` all covered), an empty,
scaffolded `gitops-<appName>` repo (its real `<cluster>/<env>/values.yaml` layout
gets populated once `ApplicationEnvironment` exists, immediately below), and a
`tenants/<appName>/app.yaml` entry in `gitops-cluster-dev-tenants` (read by that
repo's `tenant-appprojects` ApplicationSet to build the app's `AppProject`).
Deliberately narrow — no upper-env provisioning, no real CICD pipeline (this
platform's control plane isn't running on `kind-dev` yet, a separate migration
task) — the Composition reports that gap as a real `CicdOnboarded: False` custom
condition rather than silently skipping it. Three real, load-bearing corrections
found only by building this for real: GitHub Apps can't create repos under a
personal (non-Org) account at all — a classic PAT (`repo`+`delete_repo` scopes)
replaces the originally-planned GitHub App reuse; Crossplane v2 rejects a
namespaced XR composing a Cluster-scoped managed resource, so `provider-github`'s
namespaced `repo.github.m.upbound.io` family is required, not its Cluster-scoped
sibling; `function-go-templating` reserves `Ready`/`Healthy`/`Synced` and errors if
a Composition patches them directly — the custom-condition mechanism above is the
only supported way to express "succeeded except X."

**`ApplicationEnvironment` XRD + Composition — second Bootstrap-tier XRD, built
2026-08-15, extended the same day for real multi-cluster support once a second
cluster (`kind-prod`) existed to build and live-verify against.** Item 3's design:
given an already-onboarded app (`NodeJSApplication`) and an `env`
(`dev`/`staging`/`prod`, enum-constrained), pure `provider-github` commits into the
app's own `gitops-<appName>` repo and a target cluster's own
`gitops-cluster-<cluster>-tenants` — no live cluster credential. Initial
`values.yaml` is an identity-only stub (`rollout: null`) since this platform's CICD
control plane isn't running yet — surfaced as a `WorkloadDeployed: False` custom
condition (direct copy of `NodeJSApplication`'s `CicdOnboarded` mechanism).

`cluster` is a **required, real spec field**, not a hardcoded constant — gated live
against a new cluster registry (`gitops-cluster-dev/00-bootstrap/cluster-registry/`,
a `ConfigMap` per cluster, cluster-admin-authored) via a real Crossplane
extra-resources lookup, the first actual use of that mechanism in this catalog
(`function-go-templating` natively supports it — confirmed against its own v0.12.3
docs, no custom function needed). Must resolve to `type: upper` +
`crossplaneReady: "true"` or the Composition creates nothing and reports
`ClusterReady: False` instead — `type: dev` is a structural rejection (dev envs
belong to the separate `platform/envs/`-live-read mechanism, per `gitops-strategy.md`
§10). Also seeds the target cluster's own `tenants/<appName>/app.yaml` (§6 scopes
`AppProject` per cluster), with `managementPolicies` excluding `"Delete"` so one
env's teardown never deletes a file a sibling env on the same cluster still needs —
**not** `spec.deletionPolicy: Orphan`, a real live gotcha:
`provider-upjet-github` v0.19.1's `RepositoryFile` CRD has no such field at all
(confirmed via a real `ReconcileError` + `kubectl explain`, this provider version
uses `managementPolicies` exclusively).

**Live-verified end-to-end on a real second cluster**: `kind-prod` bootstrapped for
real (`gitops-cluster-kind-prod`, reusing its pre-existing ArgoCD instance rather
than standing up a second one — see that repo's own README), a scoped Crossplane
install (core + `provider-kubernetes` + `function-go-templating`/
`function-auto-ready` + just the `SLO` XRD — **not** `provider-github`, **not**
`NodeJSApplication`/`ApplicationEnvironment`, which stay `kind-dev`-only
permanently). Both the rejection path (`crossplaneReady: "false"` → `ClusterReady:
False`, zero resources) and the success path (real commits, `kind-prod`'s own
ArgoCD picking up the new tenant unprompted, a real namespace/`ServiceAccount`/
`NetworkPolicy`) proven live with a throwaway app, fully torn down after — including
confirming the orphaned `app.yaml` really does survive the env XR's own deletion, as
designed. See `idp/docs/service-catalog-design.md` §0 for the full architecture.

**`SLO` XRD + Composition — first XRD in the catalog, live-verified on
`kind-dev`.** Item 4's design (`idp/docs/service-catalog-design.md`), wraps
Sloth (sloth.dev) rather than hand-rolling multi-window-multi-burn-rate
PromQL: one `SLO` XRD (`catalog.idp.io/v1alpha1`, Crossplane v2 namespaced,
just `environmentRef`/`service`/`objective`/`indicator` - no burn-rate or
window fields, Sloth computes the full canonical pattern itself) whose
Composition (`function-go-templating`, `source: Inline` - see
`compositions/slo/build-composition.sh`) renders a Sloth
`PrometheusServiceLevel` (Sloth's own controller, installed via
`gitops-cluster-dev/10-crds-operators/sloth/`, does the
spec→`PrometheusRule` translation) and a Grafana dashboard `ConfigMap`
(kube-prometheus-stack sidecar convention). `indicator.type:
availability|latency` both covered.

Went through a hand-rolled revision first (matching a kube-slo-style article
the user found, own PromQL/burn-rate math, no Sloth dependency) before
switching - both were live-verified independently; see
`idp/docs/service-catalog-design.md` Item 4's revision history for the
reasoning. Real bugs found live along the way, worth knowing before touching
these files (full detail in the Composition/template files' own header
comments):
- A literal `<< >>` mention inside a plain YAML comment collides with the
  templating engine's own delimiter scan and breaks the whole render - the
  lexer doesn't know about YAML comment syntax. Standing gotcha called out in
  every template file's header now.
- Crossplane's controller `ServiceAccount` has no built-in RBAC for native
  resource kinds a Composition composes directly - needed extending
  `native-resources-rbac.yaml` for `PrometheusServiceLevel` (and, during the
  hand-rolled revision, `PrometheusRule`).
- **A second `Function` object pointing at the same package as an
  already-installed one doesn't just mark itself unhealthy - it corrupts
  Crossplane v2.3.4's package-manager dependency-lock graph for every OTHER
  Function on the cluster.** Tried giving the SLO Composition its own
  dedicated `function-go-templating-slo` Function + mount to keep its
  templates isolated from the ai-rollout Application Composition's own
  `/templates` mount; on a freshly-bootstrapped cluster this left
  `function-auto-ready` with no runtime Deployment at all, silently degrading
  the unrelated `widget-api` Rollout's Ready-status reporting. Fixed by
  switching to `source: Inline` instead (templates embedded directly in the
  Composition, generated from `templates/*.yaml` via `build-composition.sh`)
  - reuses the one shared `function-go-templating` Function, no new
  registration, no collision, and the templates are now fully GitOps-tracked
  in the Composition itself instead of a live-cluster-only ConfigMap.
- Sloth's own CRD claims `slo`/`slos` as `categories` (not shortNames) on
  `prometheusservicelevels` - this XRD's own `slo` shortName collided with
  that (`kubectl get slo` resolved to Sloth's category and 404'd looking for
  the wrong resource type). Removed; use `kubectl get slos.catalog.idp.io` or
  plain `slos` (this resource's actual plural, unambiguous).
- kube-prometheus-stack's `Prometheus` CR only loads a `PrometheusRule` if
  the object itself carries `release: kube-prometheus-stack` - Sloth's own
  `extraLabels` Helm value only stamps that onto each individual generated
  rule's `labels:`, not the object's `metadata.labels`. Fixed by having the
  Composition stamp `metadata.labels` on the `PrometheusServiceLevel` it
  generates; Sloth propagates that onto the `PrometheusRule` it creates.

**GitOps wiring — done, live-verified 2026-08-13.**
`gitops-cluster-dev/20-service-catalog/idp-service-catalog/application.yaml`
pins this repo to git tag `v0.1.0` via a directory-source Application (same
pattern already proven for `10-crds-operators`/`40-observability`), syncing
`xrds/*.yaml` + `compositions/*/composition.yaml` only. `charts/idp-application`
stays un-synced here — it's rendered per app-release into `gitops-<app-name>`
repos, not installed cluster-wide — and `functions/`'s packages stay
registered by pinned OCI tag in `10-crds-operators/crossplane/functions.yaml`,
not synced as source directories. Replaces the manual `kubectl apply`
verification path used until now.

**Not started yet**: the rest of the v1 XRD catalog (`SpringBootApplication` and the
Component XRDs — Redis, `OAuthServer`, ...) and their Compositions. Also not built:
the `ClusterAnalysisTemplate` golden-path library and `argocd-cm` `Rollout`
health-check config (§3 says these belong in `idp-cluster-baseline`), and a real
platform default canary step sequence (§3 "Still open" item 3 — the chart ships a
deliberately inert placeholder in the meantime, see its README). The rest of
`idp-application`'s own coverage (Rollout/Service/etc.) remains fixture-only, not
live-verified against a real `ApplicationEnvironment`-provisioned env with a real
`rollout.image` set yet — the live-verification pass above used a direct `helm
install`, not a real `ApplicationEnvironment` XR (that XRD didn't exist yet at the
time).

## Layout (so far)

```
functions/
  function-rollout-watcher/    Composition Function: watches Rollout, dispatches diagnosis
  diagnosis-holmes-dispatch/   Thin Job: hands the investigation off to HolmesGPT
charts/
  idp-application/             §3's Embedded+Attached tier chart - one release per (app, cluster, env)
xrds/
  nodejsapplication.yaml        NodeJSApplication XRD (catalog.idp.io/v1alpha1)
  applicationenvironment.yaml   ApplicationEnvironment XRD (catalog.idp.io/v1alpha1)
  slo.yaml                      SLO XRD (catalog.idp.io/v1alpha1)
compositions/
  nodejsapplication/            NodeJSApplication Composition (source: Inline) + templates + build-composition.sh
  applicationenvironment/       ApplicationEnvironment Composition (source: Inline) + templates + build-composition.sh
  slo/                          SLO Composition (source: Inline) + templates + build-composition.sh
```
