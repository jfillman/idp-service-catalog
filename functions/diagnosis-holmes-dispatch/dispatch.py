"""
diagnosis-holmes-dispatch: thin replacement for ai-rollout's bespoke
diagnosis-job/agent.py.

Where agent.py ran its own Claude tool-use loop (spawning kubernetes-mcp-server
and github-mcp-server as stdio subprocesses, holding its own
ANTHROPIC_API_KEY + GITHUB_PERSONAL_ACCESS_TOKEN), this script holds neither
credential: it makes exactly one HTTP call to an already-running, shared
HolmesGPT service (POST /api/chat), which has its own standing credentials
and its own k8s + GitHub MCP toolsets already wired up. Same investigation
protocol as before (ported from agent.py's SYSTEM_PROMPT), same env-var
contract from the Composition Function's build_diagnosis_job() (unchanged),
just handed off instead of performed locally.

Required environment variables (same names function-rollout-watcher already
sets - see fn.py's build_diagnosis_job()):
  ROLLOUT_NAME, ROLLOUT_NAMESPACE
  GITOPS_OWNER, GITOPS_REPO, GITOPS_BASE_BRANCH, GITOPS_MANIFEST_PATH
  SRC_OWNER, SRC_REPO, SRC_BASE_BRANCH, SRC_PATH

Optional:
  HOLMES_URL   - defaults to the in-cluster HolmesGPT Service
  HOLMES_MODEL - model override passed to Holmes; unset uses Holmes' own
                 configured default (claude-sonnet)

Also optional - notification backends, sent once a real Holmes result comes
back (see notify.py's own module docstring for the full contract, and for how
to add a new backend beyond Slack):
  NOTIFY_SLACK_ENABLED, NOTIFY_SLACK_CHANNEL, SLACK_WEBHOOK_URL
  NOTIFY_PAGERDUTY_ENABLED, PAGERDUTY_ROUTING_KEY (stub, not yet implemented)
"""

import json
import os
import sys

import requests

from notify import notify_all

def env(name: str, default: str = "", required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        print(f"FATAL: required env var {name} is not set", file=sys.stderr)
        sys.exit(1)
    return val


ROLLOUT_NAME = env("ROLLOUT_NAME", required=True)
ROLLOUT_NAMESPACE = env("ROLLOUT_NAMESPACE", required=True)
GITOPS_OWNER = env("GITOPS_OWNER", required=True)
GITOPS_REPO = env("GITOPS_REPO", required=True)
GITOPS_BASE_BRANCH = env("GITOPS_BASE_BRANCH", "main")
GITOPS_MANIFEST_PATH = env("GITOPS_MANIFEST_PATH", "")
SRC_OWNER = env("SRC_OWNER", GITOPS_OWNER)
SRC_REPO = env("SRC_REPO", GITOPS_REPO)
SRC_BASE_BRANCH = env("SRC_BASE_BRANCH", "main")
SRC_PATH = env("SRC_PATH", "")
HOLMES_URL = env("HOLMES_URL", "http://holmesgpt-holmes.holmesgpt.svc.cluster.local/api/chat")
HOLMES_MODEL = env("HOLMES_MODEL", "")

_SAME_REPO = (GITOPS_OWNER, GITOPS_REPO) == (SRC_OWNER, SRC_REPO)
if _SAME_REPO:
    _REPO_DESCRIPTION = f"""There is one relevant repository, {GITOPS_OWNER}/{GITOPS_REPO}
(monorepo containing both the GitOps manifest and the app's source code):
  - The GitOps manifest (this XR's own definition) is at
    `{GITOPS_MANIFEST_PATH or "(ask via k8s tools / infer from repo structure)"}`, branch `{GITOPS_BASE_BRANCH}`.
  - The app's source code is under `{SRC_PATH or "(infer from repo structure)"}`, branch `{SRC_BASE_BRANCH}`."""
else:
    _REPO_DESCRIPTION = f"""There are TWO separate repositories:
  - GitOps manifest repo: {GITOPS_OWNER}/{GITOPS_REPO}, path
    `{GITOPS_MANIFEST_PATH or "(ask via k8s tools / infer from repo structure)"}`, branch `{GITOPS_BASE_BRANCH}`.
  - App source code repo: {SRC_OWNER}/{SRC_REPO}, path
    `{SRC_PATH or "(infer from repo structure)"}`, branch `{SRC_BASE_BRANCH}`."""

ADDITIONAL_SYSTEM_PROMPT = f"""You are troubleshooting a failed Argo Rollouts canary
deployment that was automatically aborted and rolled back.

Incident:
  Rollout: {ROLLOUT_NAME}
  Namespace: {ROLLOUT_NAMESPACE}

{_REPO_DESCRIPTION}

Deployment configuration (image tag, replica count, canary steps, and any
app config values under spec.parameters) lives in the GitOps manifest.
The app's own logic lives in the source repo. Neither repo tells you in
advance which one actually needs to change for this incident -- that's
exactly what your investigation is for.

Your job, in order:
1. Investigate broadly and without assuming the cause. Look at the
   Rollout's status/conditions, any AnalysisRun(s) and why they failed,
   recent Kubernetes Events, logs from the crashing/unhealthy canary pods
   (current and previous), and any ConfigMaps or other resources it
   depends on. Follow the evidence wherever it leads rather than checking
   off a predetermined list. Form a specific, evidence-backed root cause --
   not a guess.
2. Let the root cause tell you which repo needs the fix, not the other way
   around. A bad deployment setting (image tag, replica count, a config
   value) means the GitOps manifest needs to change. A bug in the app's
   own behavior means the source repo needs to change. Sometimes it's
   genuinely ambiguous between the two -- if so, say so in the PR rather
   than guessing. Read the current file content from GitHub before
   proposing a diff, in whichever repo actually needs it.
3. Open a pull request:
   - Create a new branch off the appropriate base branch named
     `ai-fix/{ROLLOUT_NAME}-<short-description>`.
   - Commit the corrected file content to that branch, in whichever repo
     actually needs the fix.
   - Open a PR whose description contains:
     a. Root cause (what actually happened, with the specific evidence -
        log lines, event reasons, analysis failure messages, config
        values - that support it), and why it points at this repo/file
        rather than the other one.
     b. The fix, and why it resolves the root cause
     c. Any residual risk or things a human reviewer should double check
   - When you report back (response_format below), pr_title/pr_description
     must be the EXACT title/body you passed to create_pull_request - not a
     re-summarized or shortened version. They're shown verbatim to a human
     downstream who never sees this conversation, only your final answer.

Be concrete. Cite the actual pod names, event reasons, log lines, and
config values you observed rather than speculating. If the evidence is
inconclusive, say so explicitly in the PR description rather than guessing
at a fix.
"""

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "RolloutDiagnosis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "root_cause": {"type": "string"},
                "fix_repo": {"type": "string", "description": "owner/repo the fix PR targets"},
                "pr_url": {"type": ["string", "null"]},
                "pr_title": {"type": ["string", "null"], "description": "Exact title passed to create_pull_request - null if no PR was opened"},
                "pr_description": {"type": ["string", "null"], "description": "Exact body passed to create_pull_request - null if no PR was opened"},
                "summary": {"type": "string"},
            },
            "required": ["root_cause", "fix_repo", "pr_url", "pr_title", "pr_description", "summary"],
            "additionalProperties": False,
        },
    },
}


def main():
    payload = {
        "ask": (
            f"The Rollout '{ROLLOUT_NAME}' in namespace '{ROLLOUT_NAMESPACE}' "
            "was just aborted and rolled back. Please investigate and open a fix PR."
        ),
        "additional_system_prompt": ADDITIONAL_SYSTEM_PROMPT,
        "response_format": RESPONSE_FORMAT,
        "request_source": "rollout_diagnosis",
        "source_ref": f"{ROLLOUT_NAMESPACE}/{ROLLOUT_NAME}",
        "stream": False,
    }
    if HOLMES_MODEL:
        payload["model"] = HOLMES_MODEL

    print(f"Dispatching diagnosis request to {HOLMES_URL} for {ROLLOUT_NAMESPACE}/{ROLLOUT_NAME}", file=sys.stderr)
    resp = requests.post(HOLMES_URL, json=payload, timeout=600)
    resp.raise_for_status()
    result = resp.json()

    print("=== HOLMES DIAGNOSIS ===")
    print(result)

    # Only reached once Holmes has actually returned a real result (raise_for_status
    # above already exited on a transport-level failure) - see notify.py's own
    # module docstring for why that ordering matters.
    notify_all(rollout_name=ROLLOUT_NAME, rollout_namespace=ROLLOUT_NAMESPACE, result=_parse_diagnosis(result))


def _parse_diagnosis(result: dict) -> dict:
    """Real bug caught live: Holmes' /api/chat response envelope carries the
    actual response_format JSON (root_cause/fix_repo/pr_url/summary) as a
    STRING inside result['analysis'] - its own free-text answer field, which
    happens to be valid JSON because response_format constrained the model's
    output, not a nested object Holmes parses for you. notify_all() was
    reading those keys straight off the top-level envelope (which has no such
    keys - only 'analysis', 'conversation_history', token/cost stats, ...) and
    silently got nothing every time, hence every Slack message showing "none
    reported" for all three fields despite Holmes' own analysis text (and the
    real PR) being right there.

    Second real bug, caught the very next live run after the first fix:
    response_format isn't reliably honored - one run came back with a normal
    prose/markdown analysis instead of the constrained JSON, and the JSON
    parse above legitimately failed ("Expecting value: line 1 column 1"). The
    old fallback (empty dict) meant the resulting notification carried NO
    content at all, discarding a perfectly good investigation just because it
    wasn't in the expected shape. Falls back to using the raw analysis text
    as root_cause instead - degrades to "no fix_repo/PR link", not "nothing".
    """
    analysis = result.get("analysis", "")
    try:
        return json.loads(analysis)
    except (TypeError, ValueError) as e:
        print(
            f"WARNING: Holmes did not return structured JSON in 'analysis' ({e}) - "
            "falling back to its raw analysis text as root_cause",
            file=sys.stderr,
        )
        return {"root_cause": analysis} if analysis else {}


if __name__ == "__main__":
    main()
