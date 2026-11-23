"""Issue (bug report) templates: repro steps, evidence, likely root cause, next action."""
from __future__ import annotations

from typing import Any

from ..models import Account, Finding

# Finding categories that open a tracked QA-xxx issue (others only alert/log).
ISSUE_CATEGORIES = ("restriction", "connection", "permissions", "token_metadata", "check_failure")
# Warning-level categories that alert without opening an issue.
ALERT_ONLY_CATEGORIES = ("token_expiring", "permission_change", "assets", "setup")
# For categories whose root cause depends on state, the record field that selects it.
ROOT_CAUSE_KEY = {"connection": "connection_status", "restriction": "account_status"}

ROOT_CAUSES: dict[str, str | dict[str, str]] = {
    "connection": {
        "expired": "OAuth access token reached its expiry and was not refreshed (refresh token missing/expired, "
                   "or platform invalidated the session after a password/security change).",
        "disconnected": "Access was revoked from the platform side (user removed the app in platform settings) "
                        "or the account was disconnected in the Spikerz UI.",
        "error": "Platform API rejected the request; see evidence for the error payload.",
    },
    "restriction": {
        "deactivated": "The social account/Page was deactivated on the platform (by its owner or by the platform); "
                       "Spikerz keeps the record but can no longer protect it.",
        "default": "Platform trust & safety action on the test account (common for new/low-activity accounts, "
                   "logins from datacenter IPs, or automated behaviour).",
    },
    "permissions": "A scope was not granted at consent time, was unticked by the account owner, or was revoked "
                   "when the user changed their role on the Page/Organization.",
    "token_metadata": "The connection flag and the stored token record are out of sync: either the token was "
                      "refreshed without the record being updated, or the account is still flagged connected after "
                      "its token lapsed (the next real action would fail).",
    "check_failure": "The health check itself could not reach a conclusion (rate limit persisted beyond retries, "
                     "network failure, or missing credential reference).",
}

NEXT_ACTIONS = {
    "connection": "Re-authorize the account through the Spikerz UI (Connect account > {platform}) using the "
                  "owning team's credential, then re-run `spikerz-qa check --account {id}` to verify. If it "
                  "recurs within 7 days, raise with Spikerz engineering: token refresh may be failing.",
    "restriction": "Do NOT retry logins (it deepens restrictions). Owner completes the platform's verification/"
                   "appeal flow manually. If unrecoverable in 72h, retire the account (`spikerz-qa retire`) and "
                   "provision a replacement.",
    "permissions": "Reconnect via Spikerz UI and ensure all requested scopes are granted on the consent screen; "
                   "confirm the account still holds the Admin role on the Page/Organization.",
    "token_metadata": "Re-authorize the account via the Spikerz UI to force a fresh token record, then re-check. "
                      "Raise with Spikerz engineering if the record does not update: refresh bookkeeping may be broken.",
    "check_failure": "Re-run the check later; if it fails twice in a row, verify the credential reference "
                     "resolves and check the platform status page for outages.",
}


def short(message: str, limit: int = 90) -> str:
    message = message.split("; attempt")[0]
    return message if len(message) <= limit else message[: limit - 3] + "..."


def build_issue(account: Account, record: dict[str, Any], finding: Finding) -> dict[str, Any]:
    root = ROOT_CAUSES[finding.category]
    if isinstance(root, dict):
        state = record[ROOT_CAUSE_KEY[finding.category]]
        root = root.get(state) or root.get("default") or root["error"]
    return {
        "account_id": account.account_id,
        "opened_on": record["run_date"],
        "severity": finding.severity,
        "category": finding.category,
        "title": f"[{account.label}] {account.account_id} - {short(finding.message)}",
        "repro_steps": "\n".join(_repro(account, record, finding)),
        "evidence": {
            "health_check_run": record["run_id"],
            "checked_at": record["checked_at"],
            "connection_status": record["connection_status"],
            "account_status": record["account_status"],
            "token_valid": record["token_valid"],
            "token_expires_at": record["token_expires_at"],
            "missing_permissions": record["missing_permissions"],
            "findings": [f["message"] for f in record["findings"]],
            "report": f"reports/daily/{record['run_date']}.json",
        },
        "root_cause": root,
        "next_action": NEXT_ACTIONS[finding.category].format(platform=account.label, id=account.account_id),
    }



def _repro(account: Account, record: dict[str, Any], finding: Finding) -> list[str]:
    source = ("state comes from data/scenario.json for this monitoring day" if account.provider == "mock"
              else "state comes from the platform's read-only API")
    return [
        f"1. Inventory contains {account.account_id} ({account.label}, provider={account.provider}; {source}).",
        f"2. Run: spikerz-qa check --account {account.account_id}",
        f"3. Observe connection_status={record['connection_status']}, account_status={record['account_status']}, "
        f"severity={finding.severity}.",
        f"4. In the Spikerz UI, open the connected assets list and confirm the {account.label} account shows the "
        f"same state (e.g. a reconnect prompt for disconnected/expired assets).",
    ]
