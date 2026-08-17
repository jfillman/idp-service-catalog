# infisical-secretstore-operator

A small kopf (Python) operator reconciling `InfisicalProject` CRs
(`secrets.idp.io/v1alpha1`) against a self-hosted Infisical instance's real REST API.
Built for `idp/docs/service-catalog-design.md` Item 8's `SecretStore` XRD - see that
doc's "Q1" discussion for why this exists instead of `provider-terraform` or a
Crossplane Composition Function.

Deployed via `gitops-cluster-dev/10-crds-operators/infisical-secretstore-operator/` -
this directory holds only the source (Dockerfile, `main.py`, the CRD's source of
truth). See that Application's own header for the current local-image-only deploy
path (no registry yet).

## What it does

Given an `InfisicalProject` with `spec.projectName`, on create/resume it:

1. Creates an Infisical project (`shouldCreateDefaultEnvs: false` - see below)
2. Creates exactly one Infisical environment (`spec.environmentSlug`, default `shared`)
3. Creates an org-level machine identity, attaches Universal Auth, generates a client
   secret
4. Adds that identity to the project with the `admin` project role
5. Writes `clientId`/`clientSecret` into a Kubernetes Secret
   (`spec.credentialsSecretName`) in the CR's own namespace, owned by the CR

Every endpoint called was confirmed against Infisical's real, live OpenAPI spec
(`app.infisical.com/api/docs/json`, 1453 paths - the self-hosted instance's own
`/api/docs/json` is a stub, doesn't serve the real spec), not guessed or taken from a
summarized doc page alone.

## Why one Infisical environment, not the chart's default three

ESO's own `infisical` `SecretStore` provider pins exactly one `environmentSlug` per
`ClusterSecretStore` (confirmed against ESO's real CRD schema). Item 8's design wants
one store shared across an app's dev/staging/prod envs on the same cluster, split by
`secretsPath` folder, not by separate Infisical environments - so this operator
deliberately skips Infisical's default 3-environment scaffold and creates one.

## Known gaps (real, not hidden)

- **Partial-failure recovery is incomplete.** The create/resume handler is safe to
  retry for the "already fully succeeded" case (checks `status.projectId` and, more
  robustly, re-queries Infisical for a project/identity with the expected slug/name
  before creating a duplicate). But `attach_universal_auth` +
  `create_client_secret` only run when a *new* identity was just created in the same
  pass - a client secret is shown exactly once by Infisical's API, so re-running them
  against an already-configured identity would mint a second, orphaned credential
  without ever updating `credentialsSecretName`. A retry that finds an existing
  identity but a missing/deleted credentials Secret is not handled - would need a
  manual `kubectl delete infisicalproject` + recreate, or a real fix (store the
  client secret's presence in `status`, not just the identity's).
- **No image registry / CI pipeline yet** - built locally, `kind load
  image-archive`'d into kind-dev directly. Not representative of how this would run
  on kind-prod.
- **`INFISICAL_ORG_ID` discovery via JWT-claim decode is unverified** against a real
  token as of this writing - see `main.py`'s `get_org_id()`. Confirm the claim name
  once `infisical-bootstrap-secret` exists for real; set the `INFISICAL_ORG_ID` env
  var override on the Deployment if the claim isn't there.
- **`ensure_project_membership` is called on every reconcile**, not just on create -
  intentionally cheap/idempotent (re-adding an existing member is a no-op per the
  endpoint's own schema), so this isn't a bug, just worth knowing it's not
  create-only like the identity/project steps.

## Local dev loop (no registry yet)

```
podman build -t infisical-secretstore-operator:dev .
podman save localhost/infisical-secretstore-operator:dev -o /tmp/op.tar
kind load image-archive /tmp/op.tar --name dev
kubectl -n infisical rollout restart deployment/infisical-secretstore-operator
```
