"""
infisical-secretstore-operator - reconciles InfisicalProject CRs (secrets.idp.io/v1alpha1)
against a self-hosted Infisical instance's real REST API.

Why this exists instead of a Crossplane provider/Composition Function - see
idp/docs/service-catalog-design.md Item 8's "Q1" discussion: a Composition Function
is a stateless render pass with no delete-lifecycle hook, the wrong layer for a
resource with a real external CRUD lifecycle. No native `provider-infisical` exists
(confirmed: only an open feature request, Infisical/infisical#3240) - this is a
deliberately thin, purpose-built kopf controller instead of pulling in
provider-terraform + Infisical's Terraform provider, matching this project's existing
convention of small first-party Python controllers.

Every endpoint this client calls was confirmed against Infisical's real, live
OpenAPI spec (app.infisical.com/api/docs/json, 1453 paths - our own self-hosted
instance's /api/docs/json is a stub that doesn't serve the real spec), not guessed
or trusted from summarized docs alone.

Each InfisicalProject's machine identity authenticates via Kubernetes Auth, not
Universal Auth - no clientId/clientSecret is ever minted or persisted anywhere.
See configure_kubernetes_auth()'s docstring for the actual mechanism (ESO's own
controller SA token, verified live via this cluster's TokenReview API on every
login - nothing app-facing to steal, rotate, or leak).
"""
import datetime
import logging
import os
import re

import kopf
import requests
from kubernetes import client as k8s_client

API_GROUP = "secrets.idp.io"
API_VERSION = "v1alpha1"
PLURAL = "infisicalprojects"

INFISICAL_API_URL = os.environ["INFISICAL_API_URL"].rstrip("/")
ADMIN_TOKEN = os.environ["INFISICAL_ADMIN_TOKEN"]
# Explicit override - see the JWT-claim discovery fallback in get_org_id() below.
# Set this once the real claim shape is confirmed against the live bootstrap token,
# rather than guessing the Infisical CLI's internal Go struct field names.
ORG_ID_OVERRIDE = os.environ.get("INFISICAL_ORG_ID")

# Kubernetes Auth config for every InfisicalProject identity this operator creates -
# see configure_kubernetes_auth() below. K8S_HOST is this cluster's own API server as
# reachable from INFISICAL's pod (same cluster, always - a ClusterSecretStore is
# structurally local to one cluster's API, so Infisical and the workload calling it
# are never on different clusters here). Must be the FULLY-qualified in-cluster DNS
# name, not the short `kubernetes.default.svc` form - confirmed live (real failure,
# not a guess): Infisical's own backend validates this host eagerly at config-POST
# time via Node's dns.resolve4 (c-ares), which does a raw query with no
# /etc/resolv.conf search-domain expansion, unlike a normal pod's glibc getaddrinfo -
# `kubernetes.default.svc` alone hit `queryA ENOTFOUND`, `.cluster.local` fixed it.
K8S_HOST = os.environ.get(
    "INFISICAL_K8S_HOST", "https://kubernetes.default.svc.cluster.local"
)
# CA cert + a long-lived reviewer JWT for a dedicated infisical-token-reviewer SA
# bound to system:auth-delegator (gitops-cluster-dev/10-crds-operators/
# infisical-secretstore-operator/token-reviewer-rbac.yaml) - required so Infisical's
# own backend can call this cluster's TokenReview API to validate a presented SA
# token. Both sourced from that same SA's long-lived token Secret (its `ca.crt` key
# happens to be this cluster's real CA, auto-populated by the control plane - no
# separate CA source needed).
#
# Optional, not required, at module level - unlike INFISICAL_API_URL/ADMIN_TOKEN.
# spec.authMethod (see reconcile()) determines per-InfisicalProject whether these are
# ever actually used: this same operator image also runs on upper-env clusters
# (authMethod: universal there, always - see SecretStore Composition's cluster-
# registry check), which have no reason to carry Kubernetes-Auth config for a
# cluster's own TokenReview API that's never called. configure_kubernetes_auth()
# fails loudly if called with these unset, rather than failing at import time on a
# cluster that legitimately never needs them.
K8S_CA_CERT = os.environ.get("INFISICAL_K8S_CA_CERT")
K8S_TOKEN_REVIEWER_JWT = os.environ.get("INFISICAL_K8S_TOKEN_REVIEWER_JWT")
# The identity actually being verified on every login is NOT anything app-specific -
# it's ESO's own controller pod's SA token (see configure_kubernetes_auth()'s
# docstring for why). Confirmed live against kind-dev, not assumed: `kubectl get
# deploy -n external-secrets -o jsonpath='{.spec.template.spec.serviceAccountName}'`
# for the actual controller Deployment (not external-secrets-cert-controller or
# external-secrets-webhook, both separate SAs in the same chart) returned
# `external-secrets` in ns `external-secrets` - the chart's fullname-collapses-to-
# release-name default, same shape confirmed for the release itself.
ESO_SA_NAMESPACE = os.environ.get("ESO_SERVICEACCOUNT_NAMESPACE", "external-secrets")
ESO_SA_NAME = os.environ.get("ESO_SERVICEACCOUNT_NAME", "external-secrets")

_session = requests.Session()
_session.headers.update({
    "Authorization": f"Bearer {ADMIN_TOKEN}",
    "Content-Type": "application/json",
})


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:64]


def ready_condition(message: str) -> list[dict]:
    # The standard Crossplane condition shape (type/status/reason/message/
    # lastTransitionTime) - not just status.phase, which is this operator's own
    # convention and invisible to Crossplane. The SecretStore Composition's
    # function-auto-ready pipeline step only ever looks at
    # status.conditions[type=Ready].status=="True" on a composed resource to decide
    # the whole XR is ready; without this, the composite stayed "Creating" forever
    # even once this operator's own reconcile had actually succeeded (status.phase:
    # Ready underneath the whole time) - confirmed live against kind-dev's
    # checkout-api-kind-dev SecretStore XR.
    return [{
        "type": "Ready",
        "status": "True",
        "reason": "Available",
        "message": message,
        "lastTransitionTime": datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }]


def _req(method: str, path: str, not_found_ok: bool = False, **kwargs) -> dict | None:
    resp = _session.request(method, f"{INFISICAL_API_URL}{path}", timeout=15, **kwargs)
    if not_found_ok and resp.status_code == 404:
        return None
    if not resp.ok:
        raise kopf.TemporaryError(
            f"{method} {path} -> {resp.status_code}: {resp.text[:500]}", delay=15
        )
    return resp.json() if resp.content else {}


def get_org_id() -> str:
    """
    INFISICAL_ORG_ID is required, not derived. A JWT-claim-decode approach was tried
    first (the instance-admin token is a JWT, decoding its payload for an
    organizationId/orgId claim needs no extra API call) - confirmed NOT to work
    against a real bootstrap token: its actual claims are only {authTokenType, iat,
    identityAccessTokenId, identityId}, no org claim at all. The real value has to
    come from a real API call instead (GET /api/v1/identities/{identityId}, using
    the identityId decoded from that same JWT) - not worth re-doing on every pod
    start for a value that's a stable, one-time platform fact (this cluster's one
    permanent org, created once by the bootstrap Job), so it's looked up once by
    hand and committed as this Deployment's own INFISICAL_ORG_ID env var instead.
    """
    if not ORG_ID_OVERRIDE:
        raise RuntimeError("INFISICAL_ORG_ID is required - see this function's docstring")
    return ORG_ID_OVERRIDE


def find_project_by_slug(slug: str) -> dict | None:
    for project in _req("GET", "/api/v1/projects").get("projects", []):
        if project["slug"] == slug:
            return project
    return None


def create_project(name: str, slug: str) -> dict:
    body = {
        "projectName": name,
        "slug": slug,
        # False: we manage exactly one environment ourselves (see ensure_environment) -
        # per-env (dev/staging/prod) separation is by secretsPath folder, not by
        # Infisical environment, so the chart's default 3 environments would be
        # unused dead weight at best and a second, wrong scoping axis at worst.
        "shouldCreateDefaultEnvs": False,
    }
    return _req("POST", "/api/v1/projects", json=body)["project"]


def ensure_environment(project_id: str, slug: str) -> dict:
    project = _req("GET", f"/api/v1/projects/{project_id}")["project"]
    for env in project.get("environments", []):
        if env["slug"] == slug:
            return env
    body = {"name": slug.capitalize(), "slug": slug, "position": 1}
    return _req("POST", f"/api/v1/projects/{project_id}/environments", json=body)["environment"]


def find_identity_by_name(org_id: str, name: str) -> dict | None:
    # GET /api/v1/identities returns org-membership rows, not identities directly -
    # confirmed live: each item's own top-level `id` is the MEMBERSHIP row's id, not
    # the identity's (that's `identityId` / the nested `identity.id`, both equal).
    # The name lives under the nested `identity` object too, not top-level. POST
    # (create_identity) returns a genuinely flat {id, name, ...} shape, unlike this
    # endpoint - normalized to that same flat shape here so reconcile()'s
    # identity["id"]/identity["name"] usage works the same regardless of which path
    # produced it.
    for row in _req("GET", "/api/v1/identities", params={"orgId": org_id}).get("identities", []):
        inner = row.get("identity", {})
        if inner.get("name") == name:
            return {"id": row["identityId"], "name": inner["name"]}
    return None


def create_identity(org_id: str, name: str) -> dict:
    body = {"name": name, "organizationId": org_id, "role": "no-access"}
    return _req("POST", "/api/v1/identities", json=body)["identity"]


def configure_kubernetes_auth(identity_id: str):
    """
    Idempotent, safe to call on every reconcile (unlike the old Universal Auth
    flow this replaced) - GET-then-POST/PATCH, same shape as ensure_project_membership.
    No client secret is ever minted or persisted: the credential Infisical verifies
    on every login is whatever SA token ESO's own controller presents (its own
    in-pod projected token by default - see the ClusterSecretStore template's
    auth.kubernetesAuthCredentials, which deliberately leaves serviceAccountTokenPath
    unset). allowedNamespaces/allowedNames are therefore ESO's OWN identity
    (ESO_SA_NAMESPACE/ESO_SA_NAME), not this project's namespace - every InfisicalProject
    across every app gets the same two constants here. Per-app isolation still comes
    from Infisical's own project membership (this identity is only ever added to its
    one project, below) and from the ClusterSecretStore's own namespaceRegexes
    condition, exactly as it did under Universal Auth - swapping the auth *method*
    doesn't change who's allowed to reach which project.

    Every field name and the GET-returns-tokenReviewerJwt-back behavior confirmed
    against Infisical's real OpenAPI spec (app.infisical.com/api/docs/json,
    /api/v1/auth/kubernetes-auth/identities/{identityId}), same standard this whole
    client was already held to - not guessed from a docs page.
    """
    body = {
        "kubernetesHost": K8S_HOST,
        "caCert": K8S_CA_CERT,
        "verifyTlsCertificate": True,
        "tokenReviewerJwt": K8S_TOKEN_REVIEWER_JWT,
        "tokenReviewMode": "api",  # not "gateway" - Enterprise-only, and unneeded: Infisical
        # and every workload calling it are always on this same cluster by construction.
        "allowedNamespaces": ESO_SA_NAMESPACE,
        "allowedNames": ESO_SA_NAME,
        "allowedAudience": "",
    }
    if not (K8S_CA_CERT and K8S_TOKEN_REVIEWER_JWT):
        raise kopf.PermanentError(
            "authMethod: kubernetes but INFISICAL_K8S_CA_CERT/INFISICAL_K8S_TOKEN_REVIEWER_JWT "
            "aren't set on this operator instance - see token-reviewer-rbac.yaml. Real "
            "misconfiguration, not a transient error (this cluster's Deployment doesn't carry "
            "these), not something a retry would fix."
        )
    existing = _req(
        "GET", f"/api/v1/auth/kubernetes-auth/identities/{identity_id}", not_found_ok=True
    )
    method = "PATCH" if existing else "POST"
    _req(method, f"/api/v1/auth/kubernetes-auth/identities/{identity_id}", json=body)


def configure_universal_auth(identity_id: str) -> dict | None:
    """
    Universal Auth's counterpart to configure_kubernetes_auth() - used instead on
    upper-env clusters (SecretStore Composition sets spec.authMethod: universal
    there), where Kubernetes Auth can't work: Infisical only runs on kind-dev, and
    validating a token via Kubernetes Auth means Infisical calling TokenReview
    against the SAME cluster the token came from - a cross-cluster call Infisical CE
    can only do via Gateway mode, which is Enterprise-only. Decided explicitly, not a
    fallback of convenience - see idp/docs/service-catalog-design.md Item 8's
    multi-cluster revision for the real tradeoff (this reintroduces a persisted
    clientId/clientSecret for upper-env identities specifically).

    Attaching Universal Auth itself IS idempotent (confirmed against the real OpenAPI
    spec: GET returns clientId but never clientSecret, safe to re-run) - unlike
    minting a client secret, which is NOT: Infisical shows a client secret's value
    exactly once, ever. Returns the newly-minted secret value only when a secret was
    actually just minted (this identity had no Universal Auth attached yet before
    this call) - None otherwise, meaning "already configured, nothing new to write."
    Same accepted gap as this operator has always had here: a resumed reconcile that
    finds Universal Auth already attached but a missing/deleted credentials Secret
    can't recover the old secret value - needs a manual delete-and-recreate. Real,
    not hidden - see this operator's README.
    """
    existing = _req(
        "GET", f"/api/v1/auth/universal-auth/identities/{identity_id}", not_found_ok=True
    )
    if existing:
        return None
    attach_resp = _req(
        "POST", f"/api/v1/auth/universal-auth/identities/{identity_id}", json={}
    )
    client_id = attach_resp["identityUniversalAuth"]["clientId"]
    secret_resp = _req(
        "POST",
        f"/api/v1/auth/universal-auth/identities/{identity_id}/client-secrets",
        json={"description": "SecretStore XRD", "numUsesLimit": 0, "ttl": 0},
    )
    return {"clientId": client_id, "clientSecret": secret_resp["clientSecret"]}


def ensure_project_membership(project_id: str, identity_id: str, role: str = "viewer"):
    # NOT idempotent on Infisical's side, actually - the assumption this endpoint's
    # schema implied a harmless repeat-call no-op was wrong, caught live the first
    # time this code path ever ran against a real instance: a second POST for an
    # already-added identity returns a real 400 ("Identity is already a member"),
    # not a silent no-op. Retried on every reconcile anyway, so on that expected 400
    # this falls through to a PATCH of the same path instead of just swallowing the
    # error - confirmed against Infisical's real OpenAPI spec (roles: [{role}], not a
    # bare role string) - so an already-provisioned identity's role actually heals to
    # match `role` on its next reconcile instead of staying stuck at whatever it was
    # first created with.
    url = f"{INFISICAL_API_URL}/api/v1/projects/{project_id}/memberships/identities/{identity_id}"
    resp = _session.post(url, json={"role": role}, timeout=15)
    if resp.ok:
        return
    if resp.status_code == 400 and "already a member" in resp.text:
        resp = _session.patch(url, json={"roles": [{"role": role}]}, timeout=15)
        if resp.ok:
            return
    raise kopf.TemporaryError(
        f"POST/PATCH .../memberships/identities/{identity_id} -> {resp.status_code}: {resp.text[:500]}",
        delay=15,
    )


def delete_project(project_id: str, logger):
    # json={} (not no-body) - required, caught live: _session sends
    # Content-Type: application/json on every request (module-level default), and a
    # DELETE with that header but a truly empty body makes Infisical's Fastify
    # backend reject it (FST_ERR_CTP_EMPTY_JSON_BODY) - surfaced to this client as an
    # unhelpful generic 500, not the 400 Fastify's own error actually was. This
    # on_delete handler's own try/except already made the symptom invisible
    # (orphaned project, no reconcile error) - only caught by checking Infisical's
    # own project list after a real delete, not from operator logs alone.
    try:
        _req("DELETE", f"/api/v1/projects/{project_id}", json={})
    except kopf.TemporaryError as e:
        logger.warning("delete_project(%s) failed, continuing: %s", project_id, e)


def delete_identity(identity_id: str, logger):
    try:
        _req("DELETE", f"/api/v1/identities/{identity_id}", json={})
    except kopf.TemporaryError as e:
        logger.warning("delete_identity(%s) failed, continuing: %s", identity_id, e)


def write_credentials_secret(namespace: str, secret_name: str, string_data: dict, owner: dict):
    # Kubernetes-Auth mode: {"identityId": ...} only - not a credential in its own
    # right, an opaque non-secret UUID; nothing that reads this Secret can
    # authenticate as this identity with it alone (that requires presenting ESO's own
    # SA token, which this Secret says nothing about). Written on every reconcile,
    # idempotent (identityId never changes once assigned).
    #
    # Universal-Auth mode: {"clientId": ..., "clientSecret": ...} - a REAL credential,
    # written exactly once (only when configure_universal_auth() actually minted a new
    # one - see that function's docstring for why a repeat write isn't possible).
    v1 = k8s_client.CoreV1Api()
    body = k8s_client.V1Secret(
        metadata=k8s_client.V1ObjectMeta(
            name=secret_name,
            namespace=namespace,
            owner_references=[owner],
        ),
        string_data=string_data,
    )
    try:
        v1.create_namespaced_secret(namespace, body)
    except k8s_client.ApiException as e:
        if e.status == 409:
            v1.replace_namespaced_secret(secret_name, namespace, body)
        else:
            raise


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    # Disabled rather than granted `events` RBAC cluster-wide just for kopf's own
    # progress-reporting Events - this operator's own logs (kubectl logs) are enough
    # for a first pass, keeps the ClusterRole scoped to what reconciliation actually
    # needs (infisicalprojects + secrets), same instinct as this catalog's existing
    # native-resources-rbac.yaml (grant per real need, not broadly up front).
    settings.posting.enabled = False
    # Confirms the org id resolves (JWT-decode or override) before any InfisicalProject
    # reconcile is attempted, rather than failing confusingly mid-handler.
    org_id = get_org_id()
    logging.info("infisical-secretstore-operator starting, org_id=%s", org_id)


@kopf.on.create(API_GROUP, API_VERSION, PLURAL)
@kopf.on.resume(API_GROUP, API_VERSION, PLURAL)
def reconcile(spec: dict, status: dict, meta: dict, namespace: str, name: str,
              patch: kopf.Patch, logger, **_):
    org_id = get_org_id()
    project_name = spec["projectName"]
    project_slug = spec.get("slug") or slugify(project_name)
    env_slug = spec.get("environmentSlug", "shared")
    secret_name = spec.get("credentialsSecretName", f"{name}-infisical-creds")

    owner = {
        "apiVersion": f"{API_GROUP}/{API_VERSION}",
        "kind": "InfisicalProject",
        "name": name,
        "uid": meta["uid"],
        "controller": True,
        "blockOwnerDeletion": True,
    }

    project = find_project_by_slug(project_slug)
    if project is None:
        logger.info("Creating Infisical project %s (slug=%s)", project_name, project_slug)
        project = create_project(project_name, project_slug)
    project_id = project["id"]
    patch.status["projectId"] = project_id
    patch.status["projectSlug"] = project["slug"]

    env = ensure_environment(project_id, env_slug)
    patch.status["environmentSlug"] = env["slug"]

    identity_name = f"secretstore-{project_slug}"
    identity = find_identity_by_name(org_id, identity_name)
    if identity is None:
        logger.info("Creating machine identity %s", identity_name)
        identity = create_identity(org_id, identity_name)
    identity_id = identity["id"]
    patch.status["identityId"] = identity_id

    ensure_project_membership(project_id, identity_id, role="viewer")

    # authMethod is required, set by the SecretStore Composition from a cluster-
    # registry lookup - never a developer-facing choice (same "app owner never
    # touches cluster config" instinct as everything else gated by that registry).
    # "kubernetes": fully idempotent every reconcile, closes the old Universal-Auth
    # flow's known gap (resumed reconcile, existing identity, missing credentials
    # Secret) for free - this just re-derives and re-writes it.
    # "universal": NOT fully closed - see configure_universal_auth()'s own docstring
    # for why a client secret can't be idempotently re-derived the same way.
    auth_method = spec["authMethod"]
    if auth_method == "kubernetes":
        configure_kubernetes_auth(identity_id)
        write_credentials_secret(namespace, secret_name, {"identityId": identity_id}, owner)
    elif auth_method == "universal":
        new_creds = configure_universal_auth(identity_id)
        if new_creds:
            write_credentials_secret(namespace, secret_name, new_creds, owner)
    else:
        raise kopf.PermanentError(f"spec.authMethod must be 'kubernetes' or 'universal', got {auth_method!r}")

    patch.status["credentialsSecretName"] = secret_name
    patch.status["authMethod"] = auth_method
    patch.status["phase"] = "Ready"
    message = f"project={project['slug']} environment={env['slug']}"
    patch.status["message"] = message
    patch.status["conditions"] = ready_condition(message)


@kopf.on.delete(API_GROUP, API_VERSION, PLURAL)
def on_delete(status: dict, logger, **_):
    project_id = status.get("projectId")
    identity_id = status.get("identityId")
    if project_id:
        logger.info("Deleting Infisical project %s", project_id)
        delete_project(project_id, logger)
    if identity_id:
        logger.info("Deleting Infisical identity %s", identity_id)
        delete_identity(identity_id, logger)
    # The credentials Secret is not deleted here - it carries an ownerReference back
    # to this CR (see write_credentials_secret), so Kubernetes' own GC removes it,
    # same as every other composed-resource cleanup in this catalog.


# InfisicalEnvironment (secrets.idp.io/v1alpha1) - a second, deliberately thin CRD
# this same operator also reconciles. Ensures ONE additional environment exists
# inside an ALREADY-existing project (created by some other InfisicalProject CR,
# never this one) - never creates a project or an identity itself. Exists because one
# InfisicalProject == one project + one identity + one "shared" environment, but
# Option 2 (idp/docs/service-catalog-design.md Item 8's multi-cluster revision) needs
# N additional environments in that SAME project, one per ApplicationEnvironment on
# an upper cluster - rendered by idp-application's own attached/secretstore.yaml,
# alongside a per-env ClusterSecretStore pointed at this environment's slug.
ENV_PLURAL = "infisicalenvironments"


def delete_environment(project_id: str, environment_id: str, logger):
    try:
        _req(
            "DELETE", f"/api/v1/projects/{project_id}/environments/{environment_id}",
            json={},
        )
    except kopf.TemporaryError as e:
        logger.warning(
            "delete_environment(%s, %s) failed, continuing: %s", project_id, environment_id, e
        )


@kopf.on.create(API_GROUP, API_VERSION, ENV_PLURAL)
@kopf.on.resume(API_GROUP, API_VERSION, ENV_PLURAL)
def reconcile_environment(spec: dict, patch: kopf.Patch, logger, **_):
    project_slug = spec["projectSlug"]
    env_slug = spec["environmentSlug"]

    project = find_project_by_slug(project_slug)
    if project is None:
        # Real wait, not a failure - the owning InfisicalProject (rendered by the
        # SAME chart release, or an earlier one for this app/cluster) may not have
        # reconciled yet. Same eventual-consistency shape as the credentials Secret
        # not existing yet on a SecretStore's very first reconcile.
        raise kopf.TemporaryError(
            f"Infisical project '{project_slug}' not found yet - waiting for its "
            f"owning InfisicalProject", delay=15,
        )
    project_id = project["id"]
    env = ensure_environment(project_id, env_slug)

    patch.status["projectId"] = project_id
    patch.status["environmentId"] = env["id"]
    patch.status["phase"] = "Ready"
    message = f"project={project_slug} environment={env['slug']}"
    patch.status["message"] = message
    patch.status["conditions"] = ready_condition(message)


@kopf.on.delete(API_GROUP, API_VERSION, ENV_PLURAL)
def on_delete_environment(status: dict, logger, **_):
    project_id = status.get("projectId")
    environment_id = status.get("environmentId")
    # Deletes only this ONE environment, never the project - a sibling
    # ApplicationEnvironment's own InfisicalEnvironment (or the "shared" one) may
    # still need it. The owning InfisicalProject's own on_delete (above) is what
    # tears down the whole project, once every env - and every app namespace on this
    # (app, cluster) - is gone.
    if project_id and environment_id:
        logger.info("Deleting Infisical environment %s from project %s", environment_id, project_id)
        delete_environment(project_id, environment_id, logger)


# No __main__ block - run via `kopf run operator.py`, which loads the in-cluster
# kubeconfig and drives the event loop itself; kopf's own startup already configures
# the process-wide kubernetes client config that write_credentials_secret() reuses.
