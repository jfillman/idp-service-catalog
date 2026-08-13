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

**`SLO` XRD + Composition real bugs found live on `kind-dev`** (see the
Composition/template files' own header comments for full detail): (1) a
literal `<< >>` mention inside a plain comment collided with the templating
engine's own delimiter scan and broke the whole render — fixed by rewording,
now called out as a standing gotcha in every template file's header; (2)
Crossplane's controller `ServiceAccount` had no RBAC for `PrometheusRule`
(only `argoproj.io`/`batch`, from the ai-rollout Composition) — every SLO XR
failed silently with a `forbidden` event until `native-resources-rbac.yaml`
(recreated from live state, since it existed on-cluster but was never
GitOps-tracked) was extended; (3) `function-go-templating-slo` reports
`Healthy: False` (package-manager lock-graph collision with the existing
`function-go-templating` Function, both pointing at the same package
reference) but is functionally harmless — proven by a real SLO XR composing
correctly and Prometheus loading/evaluating the resulting rules against real
data anyway.

**`SLO` XRD + Composition — first XRD in the catalog, live-verified on
`kind-dev`.** Item 4's design, built hand-rolled (not wrapping Sloth/OpenSLO,
an explicit deviation from the design doc's earlier lean): one `SLO` XRD
(`catalog.idp.io/v1alpha1`, Crossplane v2 namespaced) whose Composition
(`function-go-templating`, its own dedicated `function-go-templating-slo`
Function/mount — see `compositions/slo/composition.yaml`'s header) renders a
`PrometheusRule` (recording rules per burn-rate window + the compliance
window, multi-window-multi-burn-rate alerting per Google's SRE workbook
pattern) and a Grafana dashboard `ConfigMap` (kube-prometheus-stack sidecar
convention). `indicator.type: availability|latency` both covered. See
`compositions/slo/example/README.md` for offline template-logic testing
(`render-local.sh`, no cluster needed) and the Composition/template files'
own header comments for the concrete Go-template gotchas hit along the way
(delimiter collisions with both PromQL's `[5m]` syntax and Prometheus's own
`{{ $value }}` alert-annotation templating, YAML block-scalar indentation
risk with dynamically-generated multi-line PromQL, one literal-delimiter-in-
a-comment bug caught by the offline render).

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
  slo/                         SLO Composition + function-go-templating templates + local render tooling
```
