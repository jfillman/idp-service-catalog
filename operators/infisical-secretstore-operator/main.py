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
"""
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

_session = requests.Session()
_session.headers.update({
    "Authorization": f"Bearer {ADMIN_TOKEN}",
    "Content-Type": "application/json",
})


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:64]


def _req(method: str, path: str, **kwargs) -> dict:
    resp = _session.request(method, f"{INFISICAL_API_URL}{path}", timeout=15, **kwargs)
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
    for identity in _req("GET", "/api/v1/identities", params={"orgId": org_id}).get("identities", []):
        if identity["name"] == name:
            return identity
    return None


def create_identity(org_id: str, name: str) -> dict:
    body = {"name": name, "organizationId": org_id, "role": "no-access"}
    return _req("POST", "/api/v1/identities", json=body)["identity"]


def attach_universal_auth(identity_id: str) -> str:
    resp = _req("POST", f"/api/v1/auth/universal-auth/identities/{identity_id}", json={})
    return resp["identityUniversalAuth"]["clientId"]


def create_client_secret(identity_id: str, description: str) -> str:
    body = {"description": description, "numUsesLimit": 0, "ttl": 0}
    resp = _req(
        "POST",
        f"/api/v1/auth/universal-auth/identities/{identity_id}/client-secrets",
        json=body,
    )
    return resp["clientSecret"]


def ensure_project_membership(project_id: str, identity_id: str, role: str = "admin"):
    # Idempotent by design on Infisical's side - re-adding an existing member with
    # the same role is a harmless no-op, confirmed against the endpoint's own schema
    # (no uniqueness error documented for a repeat call), so no pre-check needed here
    # unlike project/identity creation, which really do create duplicates on retry.
    _req(
        "POST",
        f"/api/v1/projects/{project_id}/memberships/identities/{identity_id}",
        json={"role": role},
    )


def delete_project(project_id: str, logger):
    try:
        _req("DELETE", f"/api/v1/projects/{project_id}")
    except kopf.TemporaryError as e:
        logger.warning("delete_project(%s) failed, continuing: %s", project_id, e)


def delete_identity(identity_id: str, logger):
    try:
        _req("DELETE", f"/api/v1/identities/{identity_id}")
    except kopf.TemporaryError as e:
        logger.warning("delete_identity(%s) failed, continuing: %s", identity_id, e)


def write_credentials_secret(namespace: str, secret_name: str, client_id: str,
                              client_secret: str, owner: dict):
    v1 = k8s_client.CoreV1Api()
    body = k8s_client.V1Secret(
        metadata=k8s_client.V1ObjectMeta(
            name=secret_name,
            namespace=namespace,
            owner_references=[owner],
        ),
        string_data={"clientId": client_id, "clientSecret": client_secret},
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
    new_identity = identity is None
    if new_identity:
        logger.info("Creating machine identity %s", identity_name)
        identity = create_identity(org_id, identity_name)
    identity_id = identity["id"]
    patch.status["identityId"] = identity_id

    ensure_project_membership(project_id, identity_id, role="admin")

    if new_identity:
        # Only the very first creation gets a client_id/client_secret pair minted -
        # attach_universal_auth + create_client_secret are NOT safe to re-run on an
        # already-configured identity (the secret is shown once, generating a second
        # one would orphan whatever's already sitting in credentialsSecretName without
        # ever updating it - not attempted here). A resumed/retried reconcile that
        # finds an existing identity but a missing/deleted credentials Secret is a
        # real gap, not solved by this first pass - see this operator's README.
        client_id = attach_universal_auth(identity_id)
        client_secret = create_client_secret(identity_id, description=f"SecretStore XRD, {project_slug}")
        write_credentials_secret(namespace, secret_name, client_id, client_secret, owner)

    patch.status["credentialsSecretName"] = secret_name
    patch.status["phase"] = "Ready"
    patch.status["message"] = f"project={project['slug']} environment={env['slug']}"


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


# No __main__ block - run via `kopf run operator.py`, which loads the in-cluster
# kubeconfig and drives the event loop itself; kopf's own startup already configures
# the process-wide kubernetes client config that write_credentials_secret() reuses.
