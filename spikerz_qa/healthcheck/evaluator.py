"""Pure evaluation: platform state (+ yesterday's check) -> severity-ranked findings.

No I/O, no clock: everything it needs is passed in, so it is trivially unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .. import clock
from ..credentials import redact
from ..models import TOKEN_EXPIRY_WARNING_DAYS, Account, Finding, worst
from ..providers import ProviderResult

RESTRICTION_SEVERITY = {"suspended": "critical", "locked": "critical", "deactivated": "error", "restricted": "error"}
CONNECTION_MESSAGES = {
    "expired": "Token/session expired - Spikerz can no longer act on this account",
    "disconnected": "Account is disconnected from Spikerz",
    "error": "Platform returned an error for the connection",
}


@dataclass
class Evaluation:
    severity: str
    findings: list[Finding] = field(default_factory=list)
    missing_permissions: list[str] = field(default_factory=list)


def evaluate(account: Account, result: ProviderResult, checked_at: datetime,
             previous: dict[str, Any] | None) -> Evaluation:
    findings: list[Finding] = []
    findings += _restriction(result)
    findings += _connection(result)
    missing = _missing_permissions(account, result)
    if missing:
        findings.append(Finding("error", "permissions", f"Missing required permissions: {', '.join(missing)}"))
    if previous:
        if result.permissions is not None:
            findings += _set_delta(previous["permissions"], result.permissions, "permission_change",
                                   "Permissions revoked since last check", "Permissions granted since last check")
        findings += _set_delta(previous["connected_assets"], result.connected_assets, "assets",
                               "Assets no longer visible", "New assets visible")
    if not result.connected_assets and result.connection_status == "connected":
        findings.append(Finding("warning", "assets", "Connected but no assets returned"))
    findings += _token_expiry(result, checked_at)
    findings += _stale_token_metadata(result, checked_at)
    if result.setup_complete is False:
        findings.append(Finding("warning", "setup", "Spikerz onboarding never completed for this account "
                                                    "(no setup steps recorded): protection features not configured"))
    findings += [Finding("warning", "platform_error", redact(e)) for e in result.errors]
    return Evaluation(worst(f.severity for f in findings), findings, missing)


def _restriction(result: ProviderResult) -> list[Finding]:
    sev = RESTRICTION_SEVERITY.get(result.account_status)
    if not sev:
        return []
    return [Finding(sev, "restriction", f"Account is {result.account_status} on the platform")]


def _connection(result: ProviderResult) -> list[Finding]:
    msg = CONNECTION_MESSAGES.get(result.connection_status)
    return [Finding("error", "connection", msg)] if msg else []


def _missing_permissions(account: Account, result: ProviderResult) -> list[str]:
    if result.permissions is None:  # platform does not expose scopes: unknown, not missing
        return []
    return sorted(set(account.required_permissions) - set(result.permissions))


def _set_delta(before: list[str], after: list[str], category: str,
               removed_msg: str, added_msg: str) -> list[Finding]:
    removed, added = sorted(set(before) - set(after)), sorted(set(after) - set(before))
    out = []
    if removed:
        out.append(Finding("warning", category, f"{removed_msg}: {removed}"))
    if added:
        out.append(Finding("info", category, f"{added_msg}: {added}"))
    return out


def _stale_token_metadata(result: ProviderResult, checked_at: datetime) -> list[Finding]:
    """Source says 'connected' but its own token record is already expired: don't trust either blindly."""
    if result.connection_status != "connected" or not result.token_expires_at:
        return []
    if clock.days_between(checked_at, result.token_expires_at) >= 0:
        return []
    return [Finding("warning", "token_metadata",
                    f"Reported as connected, but the stored token expired on {result.token_expires_at[:10]}: "
                    f"connection state and token metadata disagree")]


def _token_expiry(result: ProviderResult, checked_at: datetime) -> list[Finding]:
    if not (result.token_valid and result.token_expires_at):
        return []
    days_left = clock.days_between(checked_at, result.token_expires_at)
    if days_left > TOKEN_EXPIRY_WARNING_DAYS:
        return []
    return [Finding("warning", "token_expiring",
                    f"Token expires in {days_left:.1f} days ({result.token_expires_at[:10]})")]
