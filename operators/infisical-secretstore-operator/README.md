# infisical-secretstore-operator

A small kopf (Python) operator reconciling two CRDs against a self-hosted Infisical
instance's real REST API: `InfisicalProject` (`secrets.idp.io/v1alpha1`, one per
`(app, cluster)` pair) and `InfisicalEnvironment` (same API group, one per
non-`"shared"` environment inside an already-existing project). Built for
`idp/docs/service-catalog-design.md` Item 8's `SecretStore` XRD - see that doc's "Q1"
discussion for why this exists instead of `provider-terraform` or a Crossplane
Composition Function, and its multi-cluster revision for the per-environment
isolation design both CRDs together implement.

Runs on EVERY cluster that has an app with secrets, not just kind-dev - deployed via
`gitops-cluster-dev/10-crds-operators/infisical-secretstore-operator/` (source of
truth: Dockerfile, `main.py`, both CRDs) AND a second, cluster-specific copy at
`gitops-cluster-kind-prod/10-crds-operators/infisical-secretstore-operator/`
(deployment manifest only - same image, same CRDs, different env vars). Infisical
itself only ever runs on kind-dev (shared platform infra, installed once); every
OTHER cluster's copy of this operator talks to it over the network instead of an
in-cluster Service - see "Two auth methods" below for why that's not just a detail.

## What it does

**`InfisicalProject`** (`spec.projectName`, `spec.authMethod`), on create/resume:

1. Creates an Infisical project (`shouldCreateDefaultEnvs: false` - see below)
2. Creates exactly one Infisical environment, always named `shared`
3. Creates an org-level machine identity, configures it per `spec.authMethod` - see
   "Two auth methods" below
4. Adds that identity to the project with the `viewer` project role (read-only - this
   identity only ever needs to let ESO pull secrets, never manage the project)
5. Writes credentials into a Kubernetes Secret (`spec.credentialsSecretName`) in the
   CR's own namespace, owned by the CR - shape depends on `authMethod` (see below)

**`InfisicalEnvironment`** (`spec.projectSlug`, `spec.environmentSlug`), on
create/resume: looks up the ALREADY-existing project by slug (waits, doesn't create
one, if not found yet), ensures one additional environment exists inside it. On
delete: removes just that one environment, never the project. Exists because
`idp-application`'s own chart (`templates/attached/secretstore.yaml`) renders one of
these per `ApplicationEnvironment` on an upper cluster, alongside a `ClusterSecretStore`
narrowed to exactly that environment - real per-environment isolation (a
wrong-namespace `ExternalSecret` hard-fails, doesn't just fail an authorization check),
not a `secretsPath` convention. See `xrds/secretstore.yaml`'s own header for the full
two-mode (`"shared"` vs. per-environment) design.

## Two auth methods, chosen by cluster, never by a developer

`spec.authMethod` is set by the `SecretStore` XRD's Composition from a plain string
comparison on `spec.cluster` (`"kind-dev"` → `kubernetes`, anything else →
`universal`) - never a developer-facing field. The split is real, not cosmetic:

- **`kubernetes`** (kind-dev only): zero-persisted-credential. The credential
  Infisical verifies on every login is ESO's own controller pod's in-cluster SA token
  (checked live via this cluster's own TokenReview API), not a stored
  clientId/clientSecret pair - only the resulting `identityId` (not sensitive on its
  own) is written to the credentials Secret. See `configure_kubernetes_auth()`'s
  docstring in `main.py` for the full mechanism, confirmed against ESO's real Go
  source and Infisical's real OpenAPI spec, not guessed from docs. Only possible
  because Infisical and the workload calling it are always the SAME cluster here.
- **`universal`** (every other cluster): a REAL, persisted `clientId`/`clientSecret`
  pair, written to the credentials Secret exactly once (`configure_universal_auth()`).
  Required, not a fallback of convenience: Infisical only runs on kind-dev, and
  Kubernetes Auth would mean Infisical calling TokenReview against a DIFFERENT
  cluster's API - Infisical CE can only do that via Gateway mode, which is
  Enterprise-only. A deliberate, user-confirmed tradeoff (persisted credentials on
  upper-env clusters specifically, in exchange for not running a second Infisical
  instance) - see `idp/docs/service-catalog-design.md` Item 8's multi-cluster
  revision for the full options considered. Same accepted gap Universal Auth has
  always had here: a client secret is shown exactly once, ever, so a resumed
  reconcile that finds Universal Auth already attached but a missing/deleted
  credentials Secret can't recover the old value - needs a manual delete-and-recreate.

This requires one cluster-wide, one-time piece of infra beyond this operator itself:
`gitops-cluster-dev/10-crds-operators/infisical-secretstore-operator/token-reviewer-rbac.yaml`
- a dedicated `infisical-token-reviewer` ServiceAccount bound to
`system:auth-delegator`, whose long-lived token lets Infisical's backend call this
cluster's TokenReview API. Fully declarative (no manual `kubectl create secret` step
needed for it, unlike `infisical-secrets`/`infisical-bootstrap-credentials` in
`infisical/application.yaml`'s header) - the control plane auto-populates that
Secret's `token`/`ca.crt` once it exists.

Two Infisical-instance-level gotchas hit live, first time this whole path was ever
run end to end - both required for Kubernetes Auth to work at all, neither is
specific to this operator, and both apply to any new cluster this ever gets deployed
to (kind-prod included):

- **`kubernetesHost` must be the fully-qualified in-cluster DNS name**
  (`https://kubernetes.default.svc.cluster.local`), not the short
  `kubernetes.default.svc` form a pod's own DNS resolution would accept. Infisical's
  backend validates the host eagerly via Node's `dns.resolve4` (c-ares), which sends
  a raw query with no `/etc/resolv.conf` search-domain expansion - the short form
  fails with a real `queryA ENOTFOUND` from Infisical's own API, not a hang or a
  silent misconfiguration.
- **`ALLOW_INTERNAL_IP_CONNECTIONS=true` must be set on the Infisical instance
  itself** (`infisical/application.yaml`'s `infisical.extraEnv`, not this operator's
  own env). Off by default, this is an SSRF guard with no exception for "this is
  self-hosted and the target IS the same cluster's own API server, on purpose" -
  without it, every `kubernetesHost` config attempt fails with a real `400 Local IPs
  not allowed as URL`, since the cluster's API server necessarily resolves to a
  private IP.

Every endpoint called was confirmed against Infisical's real, live OpenAPI spec
(`app.infisical.com/api/docs/json`, 1453 paths - the self-hosted instance's own
`/api/docs/json` is a stub, doesn't serve the real spec), not guessed or taken from a
summarized doc page alone.

## Why not the chart's default three Infisical environments

`InfisicalProject` always sets `shouldCreateDefaultEnvs: false` and creates exactly
one environment, `shared`, itself - real per-environment separation is
`InfisicalEnvironment`'s job (above), not a chart-default scaffold. ESO's own
`infisical` provider pins exactly one `environmentSlug` per `ClusterSecretStore`
(confirmed against ESO's real Go source) - which is exactly why real isolation needs
one additional environment (and one additional `ClusterSecretStore`) per
`ApplicationEnvironment`, not a `secretsPath` convention inside a single shared one.

## Cross-cluster networking (kind-prod's copy of this operator)

kind-prod's own copy of this operator talks to kind-dev's Infisical over a
NodePort (`gitops-cluster-dev/10-crds-operators/infisical-secretstore-operator/
infisical-nodeport.yaml`), reachable via kind-dev's node IP - confirmed live
(`10.89.0.2:31800`, a real 200 from a kind-prod pod), not assumed. This is a
kind-cluster-local-sandbox stand-in for what a real routable endpoint would be
between genuinely separate clusters (VPN/peering/ingress) - see that Service's own
header. kind-prod's `INFISICAL_ADMIN_TOKEN` is a direct copy of kind-dev's own
`infisical-bootstrap-secret`, moved cluster-to-cluster via a piped `kubectl get -o
json | kubectl apply -f -`, never displayed/pasted anywhere - a real, deliberate
expansion of that token's blast radius (now valid on two clusters, not one), an
accepted cost of "one shared Infisical instance" flagged here, not hidden.

## Real bug found and fixed 2026-08-18: reconcile success was invisible to Crossplane

Both CRDs only ever exposed this operator's own `status.phase: Ready` convention.
The `SecretStore` Composition's `function-auto-ready` pipeline step - same
mechanism every other Composition in this catalog uses to detect a composed
resource is ready - only ever checks the standard
`status.conditions[type=Ready].status=="True"` shape, which neither CRD's schema
had a field for at all. Every `SecretStore` XR (and anything waiting on one)
therefore stayed `Creating` forever, even once this operator had actually
finished provisioning it - confirmed live on both kind-dev and kind-prod, `phase:
Ready` sitting underneath a composite stuck not-ready indefinitely.

Fixed by adding `status.conditions` to both CRDs (`crds/*.yaml`) and having
`reconcile()`/`reconcile_environment()` write a real `Ready: True` condition on
success via the new `ready_condition()` helper. Both CRDs and both cluster
Deployments (kind-dev, kind-prod) needed the update - not scoped to
`configure_kubernetes_auth()`, applies to every `InfisicalProject`/
`InfisicalEnvironment` regardless of `authMethod`.

## Known gaps (real, not hidden)

- **No promote-to-cluster mechanism** - `.github/workflows/infisical-secretstore-
  operator-ci.yaml` (added 2026-08-26) builds and pushes `ghcr.io/jfillman/
  infisical-secretstore-operator:dev` (mutable) and `:<shortsha>` (immutable,
  unused today) automatically on every push to main that touches this operator, but
  nothing updates any cluster's `deployment.yaml` or restarts the running pod - see
  "CI / publishing" below for the manual step still required after a push.
- **`INFISICAL_ORG_ID` is a required, manually-looked-up constant**, not derived at
  runtime - a JWT-claim-decode approach was tried first, confirmed live NOT to work
  (real bootstrap tokens carry no org claim at all) - see `main.py`'s `get_org_id()`
  for the full story and how the real value gets looked up per-cluster.
- **`ensure_project_membership` is called on every reconcile**, not just on create -
  intentionally cheap, but NOT a no-op on Infisical's side: a repeat POST for an
  already-added identity returns a real 400 ("Identity is already a member"), caught
  live the first time this code path ever ran against a real instance. `main.py`
  falls through to a PATCH of the same membership on that expected 400, so an
  already-provisioned identity's role self-heals to match the current `role`
  argument on its next reconcile rather than staying stuck at whatever it was first
  created with - worth knowing if you see the 400 in logs, it's expected, not a sign
  something's wrong.

## CI / publishing

`.github/workflows/infisical-secretstore-operator-ci.yaml`: a `python -m py_compile`
sanity check (no real test suite exists for this operator yet) on every PR touching
`operators/infisical-secretstore-operator/**`, then on every push to main a build +
push of both `ghcr.io/jfillman/infisical-secretstore-operator:dev` and
`:<shortsha>` (the sha tag is unused today - not wired to any cluster's
`deployment.yaml` - but there for a future rollback/promotion mechanism).

Pushing a new `:dev` image does NOT update any running cluster - each cluster's
`image:`/`imagePullPolicy: IfNotPresent` still points at the same mutable tag, so a
running pod keeps its already-pulled layer until something forces a re-pull:

```
kubectl --context kind-dev -n infisical rollout restart deployment/infisical-secretstore-operator
kubectl --context kind-prod -n infisical rollout restart deployment/infisical-secretstore-operator
kubectl --context kind-man -n infisical rollout restart deployment/infisical-secretstore-operator
```

If a node still serves the stale cached layer despite the restart, delete the pod
directly to force a fresh pull. A real promote-to-cluster mechanism (ArgoCD Image
Updater or similar, replacing this manual restart) is future work - see "Known gaps"
above.

## Local dev loop (before pushing to main)

```
podman build -t ghcr.io/jfillman/infisical-secretstore-operator:dev .
podman push ghcr.io/jfillman/infisical-secretstore-operator:dev
```

Same image/tag CI would eventually push - useful for testing a change against a real
cluster before it's merged, without waiting on/triggering the CI pipeline. Requires
already being logged in to `ghcr.io` locally (`podman login ghcr.io`) with push
access to the `jfillman` org - CI does this itself via `secrets.GITHUB_TOKEN`.
