"""function-rollout-watcher (redesigned)

This function no longer renders any app resources — the Rollout, Service,
and AnalysisTemplate are now rendered by function-go-templating (step 1 of
the pipeline) from separate files under crossplane/composition/templates/.

This function does exactly two things:
  1. Watches the *observed* live status of the Rollout that step 1 rendered.
  2. The first time it's Degraded/Error for a given revision (tracked via
     the XR's own status.lastDiagnosisRevision), renders a Kubernetes Job
     that runs the AI diagnosis agent (see ../../diagnosis-job) and writes
     status back onto the XR.

Both the Rollout (read here) and the Job (rendered here) are PLAIN native
Kubernetes resources now, not provider-kubernetes `Object` wrappers —
Crossplane v2 supports composing arbitrary native resources directly for a
namespaced XR, which is what removed most of this file's previous
complexity (no more wrap_object(), no more .status.atProvider.manifest
unwrapping).

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
from crossplane.function import logging, response
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from crossplane.function.proto.v1 import run_function_pb2_grpc as grpcv1

DEGRADED_PHASES = {"Degraded", "Error"}

# Per-XR overrides for where the diagnosis agent should open its fix PR
# against the GITOPS MANIFEST repo (i.e. this XR's own source of truth).
# Falls back to the Composition's function `input` (an org-wide default
# GitOps repo) when not set on a given XR — most apps living in a shared
# monorepo need none of these; an app in its own repo sets owner/repo.
ANNOTATION_GITOPS_OWNER = "gitops.example.org/owner"
ANNOTATION_GITOPS_REPO = "gitops.example.org/repo"
ANNOTATION_GITOPS_BASE_BRANCH = "gitops.example.org/base-branch"
ANNOTATION_GITOPS_MANIFEST_PATH = "gitops.example.org/manifest-path"

# Separate set of overrides for where the app's SOURCE CODE lives — this
# can be a different repo/path than the GitOps manifests (polyrepo) or the
# same one (monorepo). Falls back to the resolved gitops.* coordinates when
# unset, so leaving these unset just means "source lives in the same repo
# as the manifests" — a monorepo needs zero extra annotations.
ANNOTATION_SRC_OWNER = "src.example.org/owner"
ANNOTATION_SRC_REPO = "src.example.org/repo"
ANNOTATION_SRC_BASE_BRANCH = "src.example.org/base-branch"
ANNOTATION_SRC_PATH = "src.example.org/path"


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


def resolve_gitops_config(xr, cfg):
    """Per-XR annotations override the Composition input's org-wide defaults."""
    annotations = safe_get(xr, "metadata", "annotations", default={}) or {}
    return {
        "owner": safe_get(annotations, ANNOTATION_GITOPS_OWNER) or safe_get(cfg, "gitopsOwner", default=""),
        "repo": safe_get(annotations, ANNOTATION_GITOPS_REPO) or safe_get(cfg, "gitopsRepoName", default=""),
        "base_branch": safe_get(annotations, ANNOTATION_GITOPS_BASE_BRANCH) or safe_get(cfg, "gitopsBaseBranch", default="main"),
        "manifest_path": safe_get(annotations, ANNOTATION_GITOPS_MANIFEST_PATH) or safe_get(cfg, "gitopsManifestPath", default=""),
    }


def resolve_src_config(xr, gitops):
    """Per-XR annotations override; unset falls back to the already-resolved
    gitops repo coordinates — a monorepo (app source alongside its
    manifests) needs zero src.example.org annotations at all."""
    annotations = safe_get(xr, "metadata", "annotations", default={}) or {}
    return {
        "owner": safe_get(annotations, ANNOTATION_SRC_OWNER) or gitops["owner"],
        "repo": safe_get(annotations, ANNOTATION_SRC_REPO) or gitops["repo"],
        "base_branch": safe_get(annotations, ANNOTATION_SRC_BASE_BRANCH) or gitops["base_branch"],
        "path": safe_get(annotations, ANNOTATION_SRC_PATH, default=""),
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

        cfg = req.input if req.input else {}
        diagnosis_image = safe_get(cfg, "diagnosisJobImage", default="diagnosis-job:latest")

        # --- Watch the observed (live) Rollout status ---
        # Step 1 (function-go-templating) already rendered the Rollout as a
        # plain native resource named after the XR, tracked under the
        # resource-map key "rollout" (matching its
        # gotemplating.fn.crossplane.io/composition-resource-name
        # annotation in templates/rollout.yaml).
        phase = None
        revision = None
        if "rollout" in req.observed.resources:
            observed_rollout = req.observed.resources["rollout"].resource
            phase = safe_get(observed_rollout, "status", "phase")
            revision = safe_get(observed_rollout, "status", "currentPodHash")

        # function-auto-ready (the pipeline's last step) only marks a
        # resource ready if it finds a `status.conditions` entry of
        # `type: Ready, status: "True"`. Neither of these will ever satisfy
        # that: Argo Rollout uses its own condition vocabulary (Healthy /
        # Available / Progressing / Completed, never "Ready"), and
        # AnalysisTemplate is a static definition with no controller ever
        # writing status/conditions to it at all. Mark them explicitly here,
        # where we already have domain knowledge of what "ready" means for
        # each. This runs after function-go-templating rendered them, and
        # response.to(req) carries their .resource forward from req.desired,
        # so setting .ready alone (without touching .resource) is enough.
        if "analysistemplate" in req.observed.resources:
            rsp.desired.resources["analysistemplate"].ready = fnv1.Ready.READY_TRUE
        if phase == "Healthy":
            rsp.desired.resources["rollout"].ready = fnv1.Ready.READY_TRUE

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
            job_name = f"diagnosis-{xr_name}-{revision}"[:63]
            gitops = resolve_gitops_config(xr, cfg)
            src = resolve_src_config(xr, gitops)
            if not gitops["owner"] or not gitops["repo"]:
                log.warning(
                    "gitops owner/repo unresolved; dispatching diagnosis job anyway but it will "
                    "likely fail at the GitHub-PR step. Set annotations %s / %s on the XR, or "
                    "gitopsOwner/gitopsRepoName in the Composition's function input.",
                    ANNOTATION_GITOPS_OWNER, ANNOTATION_GITOPS_REPO,
                )
            job_manifest = build_diagnosis_job(job_name, xr_namespace, diagnosis_image, xr_name, gitops, src)
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
