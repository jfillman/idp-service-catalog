"""Notification backends for a completed HolmesGPT diagnosis.

notify_all() fires exactly once per dispatch, from dispatch.py's main(), only
after a real Holmes result comes back (never on transport-level failures -
Holmes unreachable, a non-2xx before any body exists - those surface as the
Job's own Failed status instead, same as before this file existed).

Config is env-var driven, one NOTIFY_<BACKEND>_ENABLED flag per backend plus
whatever else that backend needs (e.g. Slack's NOTIFY_SLACK_CHANNEL,
SLACK_WEBHOOK_URL) - set by function-rollout-watcher's build_diagnosis_job()
from the RolloutWatch XR's own spec.notifications, which in turn comes from
idp-application's chart values (notifications.slack.*), mirroring
platform-cicd's own cicd.yaml notifications.slack.{enabled,channel} shape -
see that project's docs/archive/2026-08-18-pre-refactor/notifications.md.

Adding a new backend (e.g. PagerDuty, once it's real): implement Notifier,
add a NOTIFY_<NAME>_ENABLED branch to notify_all() below, thread the
corresponding spec.notifications.<name>.* fields through fn.py the same way
Slack's already are. See PagerDutyNotifier below for the shape a stub takes.
"""

import os
import sys
from abc import ABC, abstractmethod

import requests


class Notifier(ABC):
    """One notification backend. A single send() call, best-effort - see
    notify_all's own docstring for why a failure here must never fail the Job."""

    @abstractmethod
    def send(self, *, rollout_name: str, rollout_namespace: str, result: dict) -> None:
        """result is Holmes' own response_format JSON: root_cause, fix_repo,
        pr_url, summary - see dispatch.py's RESPONSE_FORMAT."""


class SlackNotifier(Notifier):
    """POSTs to a Slack Incoming Webhook. channel is optional - only takes
    effect if the workspace's webhook app allows overriding its configured
    default channel; Slack silently ignores it otherwise, so an empty/wrong
    channel here degrades to "posts to the webhook's own default channel",
    not a hard failure."""

    def __init__(self, webhook_url: str, channel: str = ""):
        self.webhook_url = webhook_url
        self.channel = channel

    def send(self, *, rollout_name: str, rollout_namespace: str, result: dict) -> None:
        pr_url = result.get("pr_url")
        lines = [
            f"*AI-triage diagnosis complete* — `{rollout_namespace}/{rollout_name}`",
            f"*Root cause:* {result.get('root_cause') or '(none reported)'}",
            f"*Fix repo:* {result.get('fix_repo') or '(none reported)'}",
            f"*PR:* {pr_url}" if pr_url else "*PR:* none opened",
        ]
        summary = result.get("summary")
        if summary:
            lines.append(summary)
        payload = {"text": "\n".join(lines)}
        if self.channel:
            payload["channel"] = self.channel

        resp = requests.post(self.webhook_url, json=payload, timeout=15)
        resp.raise_for_status()


class PagerDutyNotifier(Notifier):
    """Not yet implemented - stub showing the extension point the user asked
    for, not a real integration.

    Would need PAGERDUTY_ROUTING_KEY (an Events API v2 integration key - the
    PagerDuty analogue of Slack's webhook URL) and a real POST to
    https://events.pagerduty.com/v2/enqueue. Also needs a real design
    decision this stub deliberately doesn't make: unlike Slack, paging a
    human has a real cost, so "every diagnosis" is probably the wrong
    trigger - likely only when Holmes' own result signals something a human
    needs to act on urgently (e.g. no PR could be opened, or root_cause is
    empty/inconclusive), not the default-success case. Left unbuilt until
    there's a real need to page on an AI-triage result, not just be told
    about it.
    """

    def __init__(self, routing_key: str):
        self.routing_key = routing_key

    def send(self, *, rollout_name: str, rollout_namespace: str, result: dict) -> None:
        raise NotImplementedError(
            "PagerDutyNotifier is a stub, see this class's own docstring. "
            "notify_all() only constructs this when NOTIFY_PAGERDUTY_ENABLED is "
            "set - that's a real misconfiguration to surface loudly (in the "
            "Job's logs, via _try_send's own warning below), not something to "
            "silently swallow as if PagerDuty were just unconfigured."
        )


def notify_all(*, rollout_name: str, rollout_namespace: str, result: dict) -> None:
    """Best-effort across every enabled backend - a broken notifier must never
    fail the diagnosis Job itself. The diagnosis and (if Holmes managed it) the
    fix PR already happened; the Job's own success/failure status should
    reflect THAT, not a downstream notification hiccup."""
    if _env_bool("NOTIFY_SLACK_ENABLED"):
        webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
        if not webhook_url:
            print(
                "NOTIFY_SLACK_ENABLED=true but SLACK_WEBHOOK_URL is empty/unset "
                "(the notify-secrets Secret may not have synced yet) - skipping "
                "Slack notification",
                file=sys.stderr,
            )
        else:
            channel = os.environ.get("NOTIFY_SLACK_CHANNEL", "")
            _try_send(SlackNotifier(webhook_url, channel), rollout_name, rollout_namespace, result)

    if _env_bool("NOTIFY_PAGERDUTY_ENABLED"):
        routing_key = os.environ.get("PAGERDUTY_ROUTING_KEY", "")
        _try_send(PagerDutyNotifier(routing_key), rollout_name, rollout_namespace, result)


def _try_send(notifier: Notifier, rollout_name: str, rollout_namespace: str, result: dict) -> None:
    try:
        notifier.send(rollout_name=rollout_name, rollout_namespace=rollout_namespace, result=result)
    except Exception as e:  # noqa: BLE001 - deliberately broad, see notify_all's own docstring
        print(f"WARNING: {type(notifier).__name__} notification failed: {e}", file=sys.stderr)


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")
