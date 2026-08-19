"""function-rollout-watcher (extra-resources redesign)

Second redesign of this function. The first (see git history / this repo's
README) swapped the diagnosis agent for a thin HolmesGPT dispatch Job. This
one swaps *how the Rollout is observed*.

Originally this function watched a Rollout composed by step 1 of its own
pipeline (function-go-templating, ported from the ai-rollout prototype). But
the real deployment path that got built since — the `idp-application` Helm
chart, deployed per cluster by ArgoCD — never gives Crossplane a hand in
creating the Rollout at all. There's no composed resource for this function
to read.

So this function now runs alone in a single-step Composition pipeline (see
compositions/rolloutwatch/composition.yaml), on a `RolloutWatch` XR that
`idp-application` renders unconditionally (same treatment as ServiceMonitor)
alongside the real Rollout. It does exactly two things:
  1. Requests the live, Helm-created Rollout as an extra/required resource
     (matched by name + namespace — same name as this XR, same namespace,
     idp-application's own naming convention) and reads its *observed*
     status from there.
  2. The first time it's Degraded/Error for a given revision (tracked via
     the XR's own status.lastDiagnosisRevision), composes a Kubernetes Job
     that dispatches to HolmesGPT (see ../../diagnosis-holmes-dispatch) and
     writes status back onto the XR.

The Job (composed here) is a PLAIN native Kubernetes resource — Crossplane
v2 supports composing arbitrary native resources directly for a namespaced
XR. The Rollout is never composed at all now, only read.

This runs per cluster (wherever the app it's watching is actually deployed),
not centralized — see idp/docs/service-catalog-design.md §0's tier-locality
design. Because of that, this function can no longer look up gitops/src repo
coordinates from a per-XR annotation the way the ai-rollout-derived version
did (that mechanism assumed a single shared repo of manifests and was never
wired to the real system). Instead it derives them deterministically from
spec.appName/cluster/env, mirroring exactly what ApplicationEnvironment's own
Composition already computes for the same app (see
resolve_gitops_config/resolve_src_config below) — no live cross-cluster
lookup needed, no extra annotations for a developer to remember.

NOTE ON SDK ERGONOMICS: this follows the patterns shown in the official
"Write a Composition Function in Python" guide
(https://docs.crossplane.io/latest/guides/write-a-composition-function-in-python/).
Struct-like fields support dict-style `[]` read access; desired
resource/composite fields are mutated via `.update(dict)` rather than direct
assignment. If your installed `crossplane-function-sdk-python` version
differs slightly, check `crossplane.function.resource` / `response` for the
current exact API.
"""

import datetime

import grpc
from crossplane.function import logging, request, response
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from crossplane.function.proto.v1 import run_function_pb2_grpc as grpcv1

DEGRADED_PHASES = {"Degraded", "Error"}
ROLLOUT_REQUIREMENT_NAME = "rollout"

# The platform's fixed GitHub owner — same constant NodeJSApplication's own
# Composition writes into every onboarded app's identity.yaml (see that
# Composition's own header for why this isn't a per-app field: `owner` is a
# ProviderConfig-wide setting, not something an app can vary). Can be
# overridden via the Composition's function `input` (cfg.gitopsOwner) if this
# ever needs to differ per cluster, but every real onboarding today uses this
# one value.
DEFAULT_GITOPS_OWNER = "jfillman"


def safe_get(d, *keys, default=None):
    """Nested [] access that tolerates missing keys/None on Struct-like or dict objects.

    ValueError is included deliberately: the underlying protobuf Struct/Value
    wrapper raises "ValueError: Value not set" (not KeyError) when reading a
    field that exists in the schema but hasn't been populated yet — e.g. on
    an XR's very first reconcile, before any composed resource has real
    observed status. Treat that the same as "missing" rather than crashing.
    """
    cur = d
    for k in keys:
        try:
            cur = cur[k]
        except (KeyError, TypeError, IndexError, ValueError):
            return default
    return cur


def resolve_gitops_config(app_name, cluster, env, cfg):
    """Deterministic GitOps manifest-repo coordinates for app_name, matching
    exactly what ApplicationEnvironment's own Composition computes when it
    bootstraps this same app's <cluster>/<env>/values.yaml (see
    compositions/applicationenvironment's render-github-resources step) —
    owner is the platform's fixed GitHub owner (cfg.gitopsOwner can override
    it, but every real onboarding uses the default), repo is always
    `gitops-<appName>`, base branch is always "main" (NodeJSApplication scaffolds
    every gitops-<appName> repo with a main branch and never offers another),
    and the manifest path is always <cluster>/<env>/values.yaml.
    """
    owner = safe_get(cfg, "gitopsOwner", default=DEFAULT_GITOPS_OWNER)
    return {
        "owner": owner,
        "repo": f"gitops-{app_name}",
        "base_branch": safe_get(cfg, "gitopsBaseBranch", default="main"),
        "manifest_path": f"{cluster}/{env}/values.yaml",
    }


def resolve_src_config(app_name, gitops):
    """The app's own source repo — same owner/base branch as the GitOps repo
    (NodeJSApplication always creates both under the same platform owner),
    named after the app itself (not gitops-<appName>), root path (this
    platform's NodeJSApplication scaffold is always a single-app monorepo —
    there's no polyrepo/subdirectory case in the real system to resolve)."""
    return {
        "owner": gitops["owner"],
        "repo": app_name,
        "base_branch": gitops["base_branch"],
        "path": "",
    }


def build_diagnosis_job(job_name, xr_namespace, diagnosis_image, xr_name, gitops, src):
    """A plain, native batch/v1 Job — no provider-kubernetes wrapping needed.

    kind-dev redesign: this Job no longer runs its own Claude tool-use loop.
    It dispatches the investigation to an already-running, shared HolmesGPT
    service (see diagnosis-holmes-dispatch/dispatch.py) instead. Two real
    consequences worth keeping visible here, not just in the dispatcher's own
    docs: no ANTHROPIC_API_KEY / GITHUB_PERSONAL_ACCESS_TOKEN env vars at all
    (Holmes holds its own standing credentials, this Job holds none), and the
    ServiceAccount below carries zero RBAC grants — the dispatcher makes one
    HTTP call, it never touches the Kubernetes API directly.
    """
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name},
        # Deliberately no metadata.namespace: Crossplane uses the XR's own
        # namespace for composed resources of a namespaced XR regardless of
        # what a template/function sets here.
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 900,
            "template": {
                "metadata": {"labels": {"app": "diagnosis-holmes-dispatch"}},
                "spec": {
                    "serviceAccountName": "diagnosis-dispatch",
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "diagnosis-holmes-dispatch",
                            "image": diagnosis_image,
                            # IfNotPresent so kubelet uses whatever image was
                            # `kind load docker-image`'d onto the node rather
                            # than trying (and failing) to pull it.
                            "imagePullPolicy": "IfNotPresent",
                            "env": [
                                {"name": "ROLLOUT_NAME", "value": xr_name},
                                {"name": "ROLLOUT_NAMESPACE", "value": xr_namespace},
                                {"name": "GITOPS_OWNER", "value": gitops["owner"]},
                                {"name": "GITOPS_REPO", "value": gitops["repo"]},
                                {"name": "GITOPS_BASE_BRANCH", "value": gitops["base_branch"]},
                                {"name": "GITOPS_MANIFEST_PATH", "value": gitops["manifest_path"]},
                                {"name": "SRC_OWNER", "value": src["owner"]},
                                {"name": "SRC_REPO", "value": src["repo"]},
                                {"name": "SRC_BASE_BRANCH", "value": src["base_branch"]},
                                {"name": "SRC_PATH", "value": src["path"]},
                            ],
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "64Mi"},
                                "limits": {"cpu": "200m", "memory": "128Mi"},
                            },
                        }
                    ],
                },
            },
        },
    }


class FunctionRunner(grpcv1.FunctionRunnerService):
    """A FunctionRunner handles gRPC RunFunctionRequests."""

    def __init__(self):
        self.log = logging.get_logger()

    async def RunFunction(
        self, req: fnv1.RunFunctionRequest, _: grpc.aio.ServicerContext
    ) -> fnv1.RunFunctionResponse:
        log = self.log.bind(tag=req.meta.tag)
        rsp = response.to(req)

        xr = req.observed.composite.resource
        xr_name = safe_get(xr, "metadata", "name")
        xr_namespace = safe_get(xr, "metadata", "namespace")
        spec = safe_get(xr, "spec", default={}) or {}
        app_name = safe_get(spec, "appName") or xr_name
        cluster = safe_get(spec, "cluster", default="")
        env = safe_get(spec, "env", default="")

        cfg = req.input if req.input else {}
        diagnosis_image = safe_get(cfg, "diagnosisJobImage", default="diagnosis-job:latest")

        # --- Watch the observed (live) Rollout status ---
        # idp-application's Helm chart renders the real Rollout directly
        # (never through this Composition) with the same name as this XR and
        # in the same namespace — see charts/idp-application/templates/
        # workload/rollout.yaml. Always declare this requirement every
        # reconcile (not just once) — Crossplane treats requirements as
        # stable once they stop changing between calls, and the first
        # reconcile after this XR is created won't have req.required_resources
        # populated yet, same "not found yet, retry next reconcile" shape
        # already used elsewhere in this catalog for extra-resources lookups.
        response.require_resources(
            rsp,
            ROLLOUT_REQUIREMENT_NAME,
            api_version="argoproj.io/v1alpha1",
            kind="Rollout",
            match_name=app_name,
            namespace=xr_namespace,
        )
        observed_rollout = request.get_required_resource(req, ROLLOUT_REQUIREMENT_NAME)
        phase = safe_get(observed_rollout, "status", "phase") if observed_rollout else None
        revision = safe_get(observed_rollout, "status", "currentPodHash") if observed_rollout else None

        prev_status = safe_get(xr, "status", default={}) or {}
        last_handled_revision = safe_get(prev_status, "lastDiagnosisRevision")

        # Only carry forward the specific keys this function owns — never
        # `dict(prev_status)` wholesale. prev_status is a live protobuf Struct
        # wrapping the XR's *entire* previous status, including
        # Crossplane-managed `conditions` we have no business rewriting.
        # Materializing it in full via dict() forces protobuf to read every
        # nested field, and any condition with a field serialized as JSON
        # `null` (e.g. an absent `message`) raises "ValueError: Value not
        # set" from google.protobuf's Struct.__getitem__ — a bare dict()
        # call has no key to pass through safe_get's try/except, so it isn't
        # protected the way every other field read in this file is.
        new_status = {
            k: v
            for k, v in {
                "rolloutPhase": safe_get(prev_status, "rolloutPhase"),
                "lastDiagnosisRevision": last_handled_revision,
                "lastDiagnosisJob": safe_get(prev_status, "lastDiagnosisJob"),
                "lastDiagnosisTime": safe_get(prev_status, "lastDiagnosisTime"),
            }.items()
            if v is not None
        }
        if phase:
            new_status["rolloutPhase"] = phase

        if phase in DEGRADED_PHASES and revision and revision != last_handled_revision:
            job_name = f"diagnosis-{app_name}-{revision}"[:63]
            gitops = resolve_gitops_config(app_name, cluster, env, cfg)
            src = resolve_src_config(app_name, gitops)
            job_manifest = build_diagnosis_job(job_name, xr_namespace, diagnosis_image, app_name, gitops, src)
            rsp.desired.resources[f"diagnosis-job-{revision}"].resource.update(job_manifest)
            new_status["lastDiagnosisRevision"] = revision
            new_status["lastDiagnosisJob"] = job_name
            new_status["lastDiagnosisTime"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            response.warning(rsp, f"Rollout {xr_name} is {phase}; dispatched diagnosis Job {job_name}")
            log.info("dispatched diagnosis job", job=job_name, revision=revision, phase=phase)
        else:
            # Keep declaring an already-dispatched job's identity every
            # reconcile — a native composed resource is pruned by Crossplane
            # the instant a function stops including its key in the
            # response, and there's no "Observe/Create only" management
            # policy for it like the old provider-kubernetes Object wrapper
            # had. Deliberately NOT re-submitting `spec`: Job.spec.template
            # is immutable after creation, and Kubernetes' immutability
            # check compares the post-merge object against the stored one —
            # omitting spec here means Crossplane's server-side-apply patch
            # simply doesn't touch that field, leaving the already-created
            # Job's template alone (which is genuinely never allowed to
            # change), while still keeping the resource in the desired set.
            if last_handled_revision and safe_get(prev_status, "lastDiagnosisJob"):
                rsp.desired.resources[f"diagnosis-job-{last_handled_revision}"].resource.update({
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "metadata": {"name": safe_get(prev_status, "lastDiagnosisJob")},
                })
            response.normal(rsp, f"Rollout {xr_name} observed phase={phase}")
            log.info("watched rollout", xr=xr_name, phase=phase)

        rsp.desired.composite.resource.update({"status": new_status})

        return rsp
