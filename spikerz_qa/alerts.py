"""Alerting: always appends to reports/alerts.jsonl; optionally posts to a webhook.

SPIKERZ_QA_ALERT_WEBHOOK (Slack/Teams incoming webhook) enables posting.
"""
from __future__ import annotations

import json
import urllib.request

from . import config
from .credentials import redact


def emit(run_date: str, account_id: str, severity: str, message: str) -> dict[str, str]:
    alert = {"run_date": run_date, "account_id": account_id, "severity": severity, "message": redact(message)}
    path = config.alerts_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(alert) + "\n")
    if (webhook := config.alert_webhook()):
        body = json.dumps({"text": f"[{severity.upper()}] {account_id}: {alert['message']}"}).encode()
        req = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as exc:  # alerting must never break the health check
            alert["delivery_error"] = str(exc)
    return alert
