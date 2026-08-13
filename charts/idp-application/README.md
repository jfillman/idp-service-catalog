# idp-application

One Helm release per `(app, cluster, env)` - renders the Embedded tier (Argo
Rollout, Service, ServiceAccount, ConfigMaps, ExternalSecret, PVCs, HPA,
PodDisruptionBudget, NetworkPolicy, AnalysisTemplates, ServiceMonitor, Jobs,
CronJobs) and the Attached tier (`components:`, `slos:` as Crossplane XRs) from
one `values.yaml`, plus a raw `extraManifests:` escape hatch. Built against
[`idp/docs/service-catalog-design.md`](https://github.com/jfillman/idp/blob/main/docs/service-catalog-design.md)
§3 - that doc is the schema's source of truth for the original Embedded/Attached
tiers; `values.yaml` in this chart documents it field-by-field, including a
handful of fields §3 didn't spell out. A second pass (ServiceAccount, Job,
CronJob, ServiceMonitor, `extraManifests:`) went beyond §3 entirely; a third
(configMaps:/secrets: consumption modes) and fourth (simplified NetworkPolicy
rules) folded back into §3's schema, since both are genuine additions to
fields §3 already specifies rather than new resource kinds - each pass called
out separately below. Real Helm chart, not pseudocode - `helm lint`/`helm
template` verified against six fixtures (minimal, full-featured incl.
blueGreen/CronJob/Job/ServiceMonitor/extraManifests, a dedicated configMaps/
secrets consumption-mode fixture, a dedicated NetworkPolicy fixture, an
`appType: infra` standalone-component release with no workload, and that same
infra release with a CronJob that supplies its own image) before any pass was
called done.

## What §3 left to this implementation, and what was decided

§3 explicitly deferred chart implementation to "a later session" and flagged a
few things as genuinely open. This is where those got resolved, concretely:

- **`rollout:` is optional** (§3's own open design question) - set it to `null`
  (or omit it) for an `appType: infra` release that's only a `components:`
  block. Implemented per the doc's stated leaning ("optional in the same
  chart... for mechanism reuse"), not the sibling-chart alternative.
- **`cluster`/`envName` are new top-level values fields**, not in §3's schema
  block. `spec.environmentRef` needs both, and a Helm release's own context
  (`Release.Name`/`Namespace`) has no structural link back to which cluster/env
  it is - the `ApplicationEnvironment` Composition that commits this file
  already knows both, so it sets them here. **Named `envName`, not `env`** -
  §3 already uses the top-level key `env` for the Embedded-tier container
  env-var list; a same-named identity field would silently collide with it in
  one flat values map (this was caught live by `helm lint` during this
  build - `range .Values.env` failed with "can't iterate over dev").
- **`secrets:` entries gained a `shared: bool` field.** Item 8 says a secret's
  Infisical path (`/dev/*` vs `/shared/*`) is "controlled by that env's own
  ExternalSecret (remoteRef), not by the store" but never says how that
  control is expressed on a `secrets:` entry - this is that mechanism. Also:
  `key` here means something different from platform-cicd's own
  `app-secrets-external-secret.yaml` `key` field (there it overrides the
  *backend* property; here it's the *env var name* exposed to the container,
  since this chart - unlike platform-cicd's - actually runs a workload that
  consumes the secret).
- **`configMaps:`/`volumes:` entries need a `mountPath`.** §3's `volumes:`
  entries already had one; `configMaps:` entries didn't (defaults to
  `/config/<name>`) - added for consistency, not a new idiom.
- **`networkPolicy.ingressControllerNamespaceSelector` - resolved 2026-08-13,
  no longer a placeholder.** `gitops-cluster-dev/10-crds-operators/contour/` was
  built and live-verified on `kind-dev` this same day - the cluster's real
  ingress controller is Contour, namespace `projectcontour`, not the
  `ingress-nginx` guess this defaulted to before. Updated to match.
- **`attachedResourceApiVersion: catalog.idp.io/v1alpha1` is a placeholder.**
  None of the `components:`/`slos:` XRDs exist yet (see `idp-service-catalog`'s
  top-level README) - this is this chart's own guess at the eventual API
  group, one value to update, not a per-template hunt, once the XRDs are
  actually authored.
- **`rollout.strategy: canary` renders a single inert `setWeight: 100` step**
  when `rollout.steps` is empty. §3 "Still open" item 3 explicitly says the
  platform's default canary step sequence "isn't designed here" - this chart
  doesn't invent one either; the placeholder behaves like an ordinary rolling
  update (no real analysis) until that default gets designed for real.
- **`podDisruptionBudget.minAvailable: 1`** is this chart's own default (§3
  only showed `{enabled: false}`) - a PDB needs `minAvailable` or
  `maxUnavailable` to mean anything; picked the conservative option.

## v1 resource-coverage pass (2026-08-13, beyond §3's schema entirely)

Added after the base chart above was already built and fixture-tested, in
response to "what else should be in v1 so we don't have to keep changing this
chart" - not part of `service-catalog-design.md` §3's original schema at all,
though `service-catalog-design.md` was updated with a pointer to this pass.

- **`serviceAccount`** - a dedicated per-app ServiceAccount (`serviceAccount.create:
  true`, name defaults to `appName`), referenced by every pod this chart renders.
  Added specifically *before* any real deployment exists, since it's the anchor
  point for scoped RBAC, private-registry pulls (`serviceAccount.imagePullSecrets`),
  and a future cloud workload-identity annotation
  (`serviceAccount.annotations`) - none of which retrofit cleanly onto pods that
  already ran as the namespace's implicit `default` SA. This chart does **not**
  create the underlying image-pull Secret itself (unlike platform-cicd's own
  `registry-credentials-external-secret.yaml`) - assumes one is already
  provisioned into the namespace by cluster/tenant onboarding. Real,
  not-yet-resolved question for whoever builds that mechanism.
- **`jobs:`/`cronJobs:`** - one-off and scheduled batch tasks, both sharing the
  main workload's `env`/`secrets`/`configMaps`/`volumes` automatically (same
  app, same config, no opt-in) and falling back to `rollout.image` when an
  entry doesn't set its own `image:` (fails fast if neither is available - e.g.
  an `appType: infra` release with no `rollout:` at all). `jobs:` entries
  default to a real Helm `pre-install,pre-upgrade` hook (`hook: true`) - the
  common "run a DB migration before the new Rollout comes up" pattern - or a
  plain, persistent Job when `hook: false`.
- **`serviceMonitor`** - on by default. `kube-prometheus-stack` is already
  installed cluster-side (per `idp_session_phase2_holmesgpt`), so every app
  gets Prometheus-scraped from its own `rollout.ports` without hand-authoring a
  ServiceMonitor. `serviceMonitor.additionalLabels` is an escape hatch for
  whatever selector label the real Prometheus CR on a given cluster requires -
  **not confirmed**, same class of placeholder as
  `networkPolicy.ingressControllerNamespaceSelector`.
- **`extraManifests: []`** - raw, arbitrary K8s objects, rendered as-is, no
  labels stamped. Same pattern `idp-cluster-baseline` already uses
  (`gitops-strategy.md` §8). The deliberate point: a future one-off need (some
  CRD nobody anticipated) never requires touching this chart's templates
  again, only this values list.
- **Shared helpers, not copy-paste**: `env`/`configMaps`/`volumes` plumbing
  used to live only inline in `rollout.yaml`; it's now three named templates
  (`idp-application.workloadEnv`/`workloadVolumeMounts`/`workloadVolumes`) so
  `job.yaml`/`cronjob.yaml` reuse it instead of re-deriving it a second and
  third time. Directly in service of "don't want to change this chart much" -
  a future change to how secrets become env vars, say, is one helper edit, not
  three template edits kept in sync by hand.

**A real bug this pass caught, worth remembering generally**: Sprig's
`default` function can't distinguish "key explicitly set to the Go zero value"
(`false`, `0`) from "key absent" - it treats both as empty and substitutes the
default either way. `.hook | default true` silently turned an explicit `hook:
false` back into `true` (confirmed live: the pre-upgrade hook annotation still
rendered), and the same pattern silently discarded explicit `backoffLimit: 0`/
`successfulJobsHistoryLimit: 0`/`failedJobsHistoryLimit: 0` (each a real,
meaningful Kubernetes setting, not "unset"). Fixed with `hasKey`-based checks
instead (a direct `{{- if or (not (hasKey . "hook")) .hook }}` for the boolean,
a new `idp-application.intOr` helper for the integers) - see that helper's own
header comment in `_helpers.tpl` for the fuller explanation and a warning
against reusing it inside an `{{- if }}` (an `include` always returns a
string, and a non-empty string like `"false"` is truthy to Go's `if` - the
same bug class relocated one level up). Worth a general caution for any future
field on this chart where `false`/`0` is a legitimate explicit value, not
"unset".

## configMap/secret consumption modes (2026-08-13, third pass)

Real gaps found in code review of the base chart: `secrets:` could only ever
become an env var (no way to mount a secret - a TLS cert, an SSH key, a
service-account JSON, anything env vars are a bad fit for); `configMaps:`
could only ever be volume-mounted (no `envFrom`-style option); and
`configMaps:` could only ever be chart-owned via `data:` - no way to reference
a ConfigMap created outside this chart, which is exactly what a Kustomize
`configMapGenerator`'s output is.

- **Both lists gained an `as: env | volume | both` field**, default unchanged
  from each list's original single-mode behavior (`volume` for `configMaps:`,
  `env` for `secrets:`). A typo'd `as:` value fails fast
  (`idp-application.resolveAs`) rather than silently matching no mode and
  producing neither an env var nor a mount with no error at all.
- **`configMaps: as: env` renders one `envFrom: [{configMapRef: ...}]` entry**
  - every key in that ConfigMap's `data:` becomes an env var verbatim (no
    per-key renaming; use the plain top-level `env:` list for a single renamed
    value instead).
- **`secrets: as: volume` does NOT create a Secret/volume per entry.** Every
  `as: volume`/`both` entry shares one `app-secrets` Secret volume (rendered
  once, only if at least one entry needs it), each mounted at its own exact
  file path via `subPath: <name>` - the standard mechanism for projecting one
  key from a Secret without a volume per key. This is also why a `secrets:`
  entry's `mountPath` means the exact file (unlike `configMaps:`'/`volumes:`'
  `mountPath`, a directory the whole ConfigMap/PVC content mounts under) -
  each `secrets:` entry is exactly one key, there's nothing to put in a
  directory.
- **`configMaps: existingConfigMap: <name>`** is the alternative to `data:`
  (mutually exclusive - exactly one required, `idp-application.configMapObjectName`
  fails fast otherwise) for referencing a ConfigMap this chart doesn't own.
  Per your call: the answer for the Kustomize-generated case specifically is a
  **fixed name** (`disableNameSuffixHash: true` on that generator), not a
  hash-suffixed one - a hash-suffixed name would need an external process
  keeping this field in sync on every regeneration, which isn't solved here.
  The tradeoff, worth remembering: that ConfigMap loses Kustomize's own
  automatic-rollout-on-content-change property; a plain data-value edit to it
  won't trigger a new Rollout revision on its own (this chart has no
  visibility into its content to checksum it - see below).
- **`checksum/configmaps`/`checksum/secrets` annotations on the Rollout's pod
  template**, closing a related gap found at the same time: previously, editing
  `configMaps[].data` or rotating a secret's backend value and running `helm
  upgrade` did NOT change the Rollout's pod template at all (same ConfigMap/
  Secret name, same keys), so Argo Rollouts never started a new canary - env
  vars especially don't update in already-running pods without a restart.
  **Real, honestly-stated limit**: `checksum/configmaps` hashes
  `configmap.yaml`'s actual rendered output, so a pure `data:` edit is fully
  covered (that data lives in `values.yaml`, visible to Helm). `checksum/secrets`
  only hashes `external-secret.yaml`'s *declaration* (which keys/paths are
  referenced) - the actual secret **value** lives in Infisical and is invisible
  to Helm at render time, so rotating it without touching `values.yaml` still
  won't trigger a new revision. That's a different, harder problem (the usual
  fix is a controller like Stakater's Reloader watching the rendered Secret
  itself, not a render-time checksum) - not solved here, flagged rather than
  silently left looking closed.

## Simplified NetworkPolicy rules (2026-08-13, fourth pass)

The existing escape hatch (`extraIngressRules`/`extraEgressRules`, raw
`NetworkPolicyIngressRule`/`EgressRule` passthrough) works but requires knowing
the real K8s `NetworkPolicyPeer`/`NetworkPolicyPort` shape - nested
`podSelector`/`namespaceSelector`/`ipBlock`, ports as `{protocol, port}`
objects. Not simple for the overwhelmingly common case: "let this other
namespace (optionally narrowed to some pods) or this CIDR reach me on this
port." `allowIngressFrom:`/`allowEgressTo:` (same shape both directions) cover
that case flatly:

```yaml
networkPolicy:
  allowIngressFrom:
    - namespace: app-payments-prod
      ports: [8080]
    - namespace: app-payments-prod
      podLabels: {app: worker}   # ANDed with namespace - narrows further
      ports: [8080, 9090]
    - cidr: 10.0.0.0/8            # external peer, instead of namespace/podLabels
      ports: [5432]
```

- `namespace:` resolves via the auto-populated `kubernetes.io/metadata.name`
  label (every namespace gets one, k8s 1.21+) - the exact same mechanism
  already used for `networkPolicy.ingressControllerNamespaceSelector`, not a
  new idiom.
- `ports:` is bare integers, TCP assumed - the dominant real-world case. UDP/
  SCTP, or multiple ORed peers in one rule, still need
  `extraIngressRules`/`extraEgressRules` - not reinvented here, both escape
  hatches coexist with the new fields.
- Each entry renders as its own self-contained rule (K8s ORs separate `ingress[]`/
  `egress[]` rules together), so entries never interact with each other or with
  the default same-namespace/ingress-controller rule.
- `namespace:`/`cidr:` are mutually exclusive per entry -
  `idp-application.networkPolicyPeer` fails fast if an entry sets both or
  neither, same fail-fast instinct as every other lookup/validation helper in
  this chart (`componentKind`, `configMapObjectName`, `resolveAs`).
- `allowEgressTo` (like `extraEgressRules` before it) correctly flips
  `policyTypes` to include `Egress` - verified live that the default,
  `allowEgressTo`-empty case is byte-identical to before this pass (no
  behavior change for existing releases that don't use the new fields).

## Not built yet

- The XRDs themselves (`NodeJSApplication`, `SpringBootApplication`,
  `ApplicationEnvironment`, `SLO`, the Component XRDs) and their Compositions -
  this chart is what a Composition renders into `gitops-<app-name>`, not a
  replacement for the Compositions.
- The `ClusterAnalysisTemplate` golden-path library (`error-rate-check`,
  `success-rate-check`) and the `argocd-cm` `Rollout` health-check
  configuration §3 says belongs in `idp-cluster-baseline`, not here.
- A real platform default canary step sequence (see above).
- Live verification on a real cluster - this has only been `helm
  lint`/`helm template` tested against fixture values so far, not installed
  against a real `ApplicationEnvironment` XR or a real Argo Rollouts
  controller.
