# idp-service-catalog

Crossplane XRDs, Compositions, Composition Functions, and the
`idp-application` Helm chart for [Dream IDP](https://github.com/jfillman/idp)
— the service catalog a Backstage-driven Crossplane plugin turns into
self-service templates. Design lives in `idp`'s
[`docs/service-catalog-design.md`](https://github.com/jfillman/idp/blob/main/docs/service-catalog-design.md);
this repo is where it's actually built.

## Status

**`SpringBootApplication` XRD + Composition — Item 1/2's second Bootstrap-tier stack,
done, live-verified end-to-end on `kind-dev` 2026-08-24.** `xrds/springbootapplication.yaml`
+ `compositions/springbootapplication/` — a structural port of `NodeJSApplication` onto
Java/Spring Boot: same devCluster-gated onboarding mechanism, same six-field
`identity.yaml`, same `CicdOnboarded`-not-`Ready` status convention. Stack-specific
pieces: `spec.javaVersion`/`spec.buildTool` (replacing `nodeVersion`/`packageManager`)
and `spec.groupId` (Maven groupId / Gradle group, and this app's single flat Java
package — see the XRD's own header for why it's not `groupId`+artifactId nested). The
Dockerfile is genuinely multi-stage (JDK build stage, bare JRE runtime stage), a real
difference from `NodeJSApplication`'s single-stage script, not just a style choice.
Live-verified via a throwaway `springbootapp-verify-test` app (real `Repository`/
`RepositoryFile` creation against GitHub, `pom.xml`/`identity.yaml`/
`xr-requests/secretstore.yaml` content confirmed via the GitHub API,
`DevClusterReady`/`CicdOnboarded`/`Ready` all reached `True`); the `gradle` `buildTool`
branch was exercised via `crossplane render` only (`example/xr-gradle-custom.yaml`), not
a real cluster apply. See `idp/docs/service-catalog-design.md` Item 1/2 for the full
writeup, including a revisited-and-declined "shared Function" refactor decision now
that both stacks are built.

**`SecretStore` XRD + `infisical-secretstore-operator` — Item 8, done, live-verified
end-to-end 2026-08-17.** `xrds/secretstore.yaml` + `compositions/secretstore/`
render an `InfisicalProject` CR (reconciled by a new kopf/Python operator,
`operators/infisical-secretstore-operator/`, against Infisical's real REST API -
no native Crossplane provider exists, ruled out `provider-terraform` too, see the
operator's own README for the "Q1" reasoning) and an ESO `ClusterSecretStore`
wrapped in a `provider-kubernetes` `Object` (Crossplane v2 rejects composing a
cluster-scoped resource directly from this namespaced XR - same fix
`NodeJSApplication`'s `provider-github` already needed). Full chain live-proven on
`kind-dev`: real Infisical project + identity + Universal Auth credentials → a
`Ready: True` `ClusterSecretStore` → a real secret written via Infisical's API →
pulled by a real `ExternalSecret` into a real K8s Secret with the correct value.
Also fixed a real, pre-existing bug this surfaced in `idp-application`'s own
`ExternalSecret` template (`remoteRef.property` broke every pull against
Infisical).

**Kubernetes Auth swap + full multi-cluster auto-provisioning (Item 8's
multi-cluster revision) — done, live-verified end-to-end on both `kind-dev` and
`kind-prod` 2026-08-17.** Two real, separate builds, same day:

1. Every `InfisicalProject` identity now authenticates via Kubernetes Auth by
   default (no `clientId`/`clientSecret` minted or persisted anywhere - ESO's own
   controller SA token, verified live via TokenReview) instead of the original
   Universal Auth. Four real bugs found and fixed live getting this working the
   first time: `kubernetesHost` needs the fully-qualified in-cluster DNS name (a raw
   c-ares query, no search-domain expansion); Infisical's SSRF guard blocks private
   IPs unless `ALLOW_INTERNAL_IP_CONNECTIONS=true`; `ensure_project_membership` was
   never actually idempotent (a real 400 on retry, contrary to the original,
   untested comment); `_session`'s own `Content-Type: application/json` default
   broke every empty-body `DELETE` call (Fastify rejects it, surfaced as an
   unhelpful 500).
2. `SecretStore`'s auto-provisioning (the "Not yet built" item above) is real now,
   with a real design change along the way: `environmentSlug` (already on the XRD)
   now has TWO modes - `"shared"` (the default, original behavior) or any other
   value, which creates ONE additional Infisical environment inside the
   already-existing project plus a SEPARATE `ClusterSecretStore` narrowed to
   exactly one namespace - real per-environment isolation (proven live: a
   correctly-scoped `ExternalSecret` pulls the right value, a wrong-namespace one
   hard-fails), not a `secretsPath` convention. `idp-application`'s own chart
   (`templates/attached/secretstore.yaml`, always-on like `NetworkPolicy`) is what
   actually triggers this now, not `ApplicationEnvironment`'s Composition directly -
   `ApplicationEnvironment` and `NodeJSApplication` are both `provider-github`-only,
   neither can create a native resource on any cluster, including kind-dev's own.
   New `InfisicalEnvironment` CRD (same operator) ensures one environment exists in
   an already-existing project. Upper-cluster stores (kind-prod) use Universal
   Auth, not Kubernetes Auth - Infisical only runs on kind-dev, and validating a
   token from a different cluster needs Gateway mode (Enterprise-only) - a real,
   deliberate, user-confirmed tradeoff, not an oversight. Proven genuinely
   cross-cluster, not simulated: a real secret written into Infisical on `kind-dev`,
   pulled by a real `ExternalSecret` on `kind-prod`, over a live-verified NodePort
   path (`infisical-nodeport.yaml`) - a kind-cluster-local-sandbox stand-in for a
   real routable endpoint between genuinely separate clusters, flagged as such, not
   assumed reusable as-is. One more real bug caught live: Crossplane's own core
   controller had no RBAC for the new `infisicalenvironments` CRD on either
   cluster - `native-resources-rbac.yaml` needed extending on both.

See `idp/docs/service-catalog-design.md` Item 8 for the full design writeup and
`operators/infisical-secretstore-operator/README.md` for the operator-level detail.

**SecretStore provisioning moved to the Bootstrap XRs (xr-requests), off
idp-application's chart — done, live-verified against real existing apps on both
clusters 2026-08-18.** Real user objection to the above: an always-on chart
template meant secrets infrastructure only existed once someone shipped an actual
release, not when an app/env was onboarded. `NodeJSApplication` and
`ApplicationEnvironment` now each commit a `SecretStore` XR manifest via
`xr-requests` instead (`idp-application`'s `attached/secretstore.yaml` deleted
entirely) - real nuance recorded in the design doc: `NodeJSApplication` could have
composed it directly (same-cluster), `ApplicationEnvironment` structurally can't
(cross-cluster, the same "no cluster holds another's API credential" constraint
that already kept `AppProject` ownership out of direct composition). kind-prod
needed its own `xr-requests` mechanism for the first time (Bootstrap XRs never ran
on upper clusters before). Also fixed the same day: `external-secret.yaml` never
actually routed secrets through the per-environment stores the prior revision
built - every secret silently still went through the old shared-store/path
convention. Fixed via ESO's real per-entry `sourceRef.storeRef` override.

See `idp/docs/service-catalog-design.md` Item 8's third revision for the full
writeup, including two real bugs found live from the old and new mechanisms
briefly coexisting mid-migration (not flaws in either design on its own).

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

**`env` opened up from a closed enum to team-chosen names, live-verified 2026-08-15.**
Was `enum: ["dev", "staging", "prod"]`; traced every real consumer (this Composition's
own template, the `tenant-onboarding` `ApplicationSet`, the `AppProject`'s own
`destinations` wildcard) and confirmed nothing branches on the specific value — pure
path/name interpolation, not encoded business logic. Replaced with a `pattern`
matching Kubernetes' own DNS-1123 namespace-label rule plus `maxLength: 20`, so an
invalid value still fails at XR admission instead of downstream. Confirmed live on
`kind-dev`: a previously-impossible custom name (`perf-test`) reconciles end-to-end
for real; an invalid one (`Staging!`) is rejected at admission with a clear
pattern-mismatch error.

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

**`AppProject`/`Application` deletion-ordering bug — fixed via `protection.
crossplane.io` `Usage`, live-verified `v0.3.2`.** A real, twice-confirmed bug: deleting
a `NodeJSApplication` while an `ApplicationEnvironment` still referenced it (via
`spec.appName`) could strand the dependent ArgoCD `Application` if its `AppProject`
got pruned before that `Application`'s own finalizer finished. Originally planned as a
homegrown extra-resources lookup on `NodeJSApplication`'s own Composition — superseded
before building it: `kubectl api-resources` on `kind-dev` confirmed Crossplane
already ships a real primitive for exactly this, `protection.crossplane.io/v1beta1`
`Usage` ("defines a deletion blocking relationship between two resources"), enforced
by an already-installed `crossplane-no-usages` admission webhook (nothing new to
deploy). `ApplicationEnvironment`'s Composition now composes one unconditionally
(`spec.of` = the parent `NodeJSApplication`, `spec.by` = itself) —
`NodeJSApplication`'s own Composition needed **zero** changes, since the webhook and
Usage controller do all the blocking purely by watching `Usage` objects. Live-verified
end-to-end on `kind-dev` with a real throwaway app+env: confirmed the `Usage` object
and the `crossplane.io/in-use` label it drives on the app, confirmed `kubectl delete`
on the app is cleanly rejected at admission time (not a finalizer hang) while the env
exists, confirmed the `Usage` is garbage-collected the moment the env is deleted, and
confirmed app deletion then succeeds. One real unknown resolved live along the way:
`function-auto-ready`'s handling of a composed `Usage` resource hadn't been exercised
in this catalog before — confirmed it reports `Ready: True` correctly, no stuck
parent readiness.

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
