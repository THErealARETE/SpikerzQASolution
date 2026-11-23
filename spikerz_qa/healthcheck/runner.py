"""Orchestrates one daily run. Each step is a small function with one job:

    check_account   -> call provider (with retry) and evaluate
    sync_inventory  -> write observed state back to the account record
    log_changes     -> turn the diff into maintenance-log events
    track_issues    -> open/close QA-xxx issues and send alerts
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable

from .. import alerts, clock
from ..credentials import redact
from ..db import Store
from ..models import Account, Finding, at_least, worst
from ..providers import PermanentProviderError, Provider, TransientProviderError
from ..retry import with_retry
from .evaluator import evaluate
from .issues import ALERT_ONLY_CATEGORIES, ISSUE_CATEGORIES, build_issue

BOT = "healthcheck-bot"
# Inventory field -> maintenance-log event type.
CHANGE_EVENTS = {"connection_status": "connection_change", "status": "status_change",
                 "permissions": "permission_change", "connected_assets": "assets_change"}
# Finding category -> maintenance-log event type (findings worth a log line without an issue).
FINDING_EVENTS = {"token_expiring": "token_expiry_warning", "retry": "transient_failure",
                  "setup": "setup_incomplete"}


def run_daily(store: Store, provider: Provider, now: datetime | None = None, actor: str = BOT,
              account_id: str | None = None, sleep: Callable[[float], None] | None = None,
              notify: bool = True) -> list[dict[str, Any]]:
    """Check every active account once for *today* (the date of ``now``)."""
    now = now or clock.now()
    run_id = uuid.uuid4().hex[:12]
    candidates = [store.get_account(account_id)] if account_id else store.list_accounts()
    accounts = [a for a in candidates if a and provider.handles(a)]
    records = []
    for account in accounts:
        previous = store.previous_check(account.account_id, now.date().isoformat())
        record = check_account(provider, account, now, run_id, previous, sleep)
        store.save_health_check(record)
        diff = sync_inventory(store, account, record, actor)
        log_changes(store, account, record, previous, diff, actor)
        track_issues(store, account, record, actor, notify)
        records.append(record)
    _log(store, record_date=now, event="health_check_run", actor=actor, message=_run_summary(run_id, records))
    for action in provider.scripted_actions(now.date()):
        _log(store, now, "corrective_action", f"{action['description']} [scripted mock operator]",
             "mock-operator", action["account_id"])
    return records


# --------------------------------------------------------------------- steps
def check_account(provider: Provider, account: Account, now: datetime, run_id: str,
                  previous: dict[str, Any] | None, sleep: Callable[[float], None] | None) -> dict[str, Any]:
    try:
        result, attempts, transient = with_retry(lambda: provider.check(account, now.date()),
                                                 **({"sleep": sleep} if sleep else {}))
    except (TransientProviderError, PermanentProviderError) as exc:
        return _record(run_id, now, account, severity="warning", attempts=3 if isinstance(exc, TransientProviderError) else 1,
                       source=account.provider, account_status=account.status, connection_status="error",
                       token_valid=None, token_expires_at=None, connected_assets=list(account.connected_assets),
                       permissions=list(account.permissions), missing_permissions=[],
                       findings=[Finding("warning", "check_failure",
                                         f"Health check could not complete: {redact(str(exc))}")])
    ev = evaluate(account, result, now, previous)
    if transient:
        ev.findings.insert(0, Finding("info", "retry", f"Recovered after {attempts} attempts ({transient[-1]})"))
        ev.severity = worst([ev.severity, "info"])
    return _record(run_id, now, account, severity=ev.severity, attempts=attempts, source=result.source,
                   account_status=result.account_status, connection_status=result.connection_status,
                   token_valid=result.token_valid, token_expires_at=result.token_expires_at,
                   connected_assets=result.connected_assets, permissions=result.permissions or [],
                   missing_permissions=ev.missing_permissions, findings=ev.findings)


def sync_inventory(store: Store, account: Account, record: dict[str, Any], actor: str) -> dict[str, Any]:
    changes: dict[str, Any] = {"last_health_check": record["checked_at"]}
    if not _has(record, "check_failure"):  # unknown state: never overwrite inventory with a guess
        changes.update(status=record["account_status"], connection_status=record["connection_status"],
                       connected_assets=record["connected_assets"])
        if record["permissions"]:  # empty = platform did not expose scopes
            changes["permissions"] = record["permissions"]
    return store.update_account(account.account_id, changes, actor, record["checked_at"], action="healthcheck_sync")


def log_changes(store: Store, account: Account, record: dict[str, Any], previous: dict[str, Any] | None,
                diff: dict[str, Any], actor: str) -> None:
    now = datetime.fromisoformat(record["checked_at"])
    for field, event in CHANGE_EVENTS.items():
        if field in diff:
            _log(store, now, event, _describe_change(field, diff[field]), actor, account.account_id)
    for f in record["findings"]:
        if f["category"] in FINDING_EVENTS:
            _log(store, now, FINDING_EVENTS[f["category"]], f["message"], actor, account.account_id)
    if _token_renewed(previous, record):
        _log(store, now, "token_refresh", f"Token renewed; new expiry {record['token_expires_at'][:10]}",
             actor, account.account_id)


def track_issues(store: Store, account: Account, record: dict[str, Any], actor: str, notify: bool) -> None:
    now = datetime.fromisoformat(record["checked_at"])
    for f in map(lambda d: Finding(**d), record["findings"]):
        if not at_least(f.severity, "warning"):
            continue
        if f.category in ISSUE_CATEGORIES:
            issue_id, created = store.open_issue(build_issue(account, record, f))
            if created:
                _log(store, now, "issue_opened", f"{issue_id} [{f.severity}] {f.message}", actor, account.account_id)
            message = f"{issue_id}: {f.message}"
        elif f.category in ALERT_ONLY_CATEGORIES:
            message = f.message
        else:
            continue
        if notify:
            alerts.emit(record["run_date"], account.account_id, f.severity, message)

    still_failing = {f["category"] for f in record["findings"]}
    for issue_id in store.close_issues(account.account_id, record["run_date"],
                                       [c for c in ISSUE_CATEGORIES if c not in still_failing]):
        _log(store, now, "issue_resolved", f"{issue_id} no longer reproduces - closed", actor, account.account_id)


# ------------------------------------------------------------------- helpers
def _record(run_id: str, now: datetime, account: Account, findings: list[Finding], **fields: Any) -> dict[str, Any]:
    return {"run_id": run_id, "run_date": now.date().isoformat(), "checked_at": clock.iso(now),
            "account_id": account.account_id, "platform": account.platform,
            "findings": [f.to_dict() for f in findings], **fields}


def _has(record: dict[str, Any], category: str) -> bool:
    return any(f["category"] == category for f in record["findings"])


def _describe_change(field: str, change: dict[str, Any]) -> str:
    before, after = change["from"], change["to"]
    if isinstance(after, list):
        removed, added = sorted(set(before) - set(after)), sorted(set(after) - set(before))
        return f"{field}: removed {removed or '-'}; added {added or '-'}"
    return f"{field}: {before} -> {after}"


def _token_renewed(previous: dict[str, Any] | None, record: dict[str, Any]) -> bool:
    """A forward jump in expiry beyond normal daily drift means a refresh/re-auth happened."""
    if not (previous and previous["token_expires_at"] and record["token_expires_at"] and record["token_valid"]):
        return False
    return clock.days_between(previous["token_expires_at"], record["token_expires_at"]) > 1.5


def _run_summary(run_id: str, records: list[dict[str, Any]]) -> str:
    bad = sum(at_least(r["severity"], "error") for r in records)
    warn = sum(r["severity"] == "warning" for r in records)
    return f"Daily health check {run_id}: {len(records)} accounts, {bad} error/critical, {warn} warning"


def _log(store: Store, record_date: datetime, event: str, message: str, actor: str,
         account_id: str | None = None) -> None:
    store.log_maintenance(record_date.date().isoformat(), event, message, actor, account_id, clock.iso(record_date))
