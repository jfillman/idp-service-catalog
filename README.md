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

**Not started yet**: the rest of the v1 XRD catalog (`NodeJSApplication`,
`SpringBootApplication`, `ApplicationEnvironment`, and the Component XRDs —
Redis, `OAuthServer`, ...) and their Compositions — `idp-application` is what
a Composition will render into `gitops-<app-name>`, but nothing calls it yet
for those. Also not built: the `ClusterAnalysisTemplate` golden-path library
and `argocd-cm` `Rollout` health-check config (§3 says these belong in
`idp-cluster-baseline`), and a real platform default canary step sequence (§3
"Still open" item 3 — the chart ships a deliberately inert placeholder in the
meantime, see its README). Also explicitly out of scope so far: wiring
`gitops-cluster-dev/20-service-catalog` (still an empty placeholder) or
pinning this repo into the app-of-apps — today's live-verification is direct/
manual `kubectl apply`, same as how `function-rollout-watcher` was proven out
before real GitOps adoption. The rest of `idp-application`'s own coverage
(Rollout/Service/etc.) remains fixture-only, not live-verified against a real
`ApplicationEnvironment` XR yet.

## Layout (so far)

```
functions/
  function-rollout-watcher/    Composition Function: watches Rollout, dispatches diagnosis
  diagnosis-holmes-dispatch/   Thin Job: hands the investigation off to HolmesGPT
charts/
  idp-application/             §3's Embedded+Attached tier chart - one release per (app, cluster, env)
xrds/
  slo.yaml                     SLO XRD (catalog.idp.io/v1alpha1)
compositions/
  slo/                         SLO Composition (source: Inline) + templates + build-composition.sh
```
