"""Report generation. Every run rebuilds all outputs from the database, so reports
always contain Day 1..today and earlier days are never edited by hand.

Outputs: daily JSON/CSV/HTML, cumulative dashboard, maintenance log, issue log, inventory export.
"""
from __future__ import annotations

import csv
import html
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from . import clock
from .db import Store
from .models import PLATFORM_LABEL, PLATFORMS, SEVERITIES, join_list, worst

MONITORING_TARGET_DAYS = 5
CSV_FIELDS = ["run_date", "checked_at", "account_id", "platform", "account_status", "connection_status",
              "token_valid", "token_expires_at", "connected_assets", "permissions", "missing_permissions",
              "severity", "attempts", "source", "findings"]

STYLE = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#1c2230;--muted:#5d6678;--line:#e2e5eb;--bn-bg:#fff4d6;--bn-ink:#5c4400;
--bn-line:#f0d58a;--ok:#1f8a4c;--info:#2f6fd6;--warning:#b7791f;--error:#c53030;--critical:#7b1fa2}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b1f27;--ink:#e7eaf0;--muted:#9aa3b5;
--line:#2c323d;--bn-bg:#3a3110;--bn-ink:#f4e2a8;--bn-line:#6b5a1d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:24px 16px}
main{max-width:1200px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.muted{color:var(--muted)}.banner{background:var(--bn-bg);color:var(--bn-ink);border:1px solid var(--bn-line);
padding:10px 12px;border-radius:8px;margin:12px 0}.cards{display:grid;
grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}.card{background:var(--card);
border:1px solid var(--line);border-radius:10px;padding:12px}.card b{display:block;font-size:22px}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:640px}th,td{padding:8px 10px;
border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;color:var(--muted);
text-transform:uppercase;letter-spacing:.03em}.pill{display:inline-block;padding:1px 8px;border-radius:999px;
color:#fff;font-size:12px;font-weight:600}.ok{background:var(--ok)}.info{background:var(--info)}
.warning{background:var(--warning)}.error{background:var(--error)}.critical{background:var(--critical)}
.pending{background:var(--line);color:var(--muted)}.nowrap{white-space:nowrap}td.cell{text-align:center}ul{margin:0;padding-left:18px}
"""


# --------------------------------------------------------------- HTML helpers
def _esc(value: Any) -> str:
    return html.escape(str(value))


def _pill(sev: str) -> str:
    return f'<span class="pill {sev}">{sev}</span>'


def _cards(items: dict[str, Any]) -> str:
    return "<div class='cards'>" + "".join(
        f"<div class='card'><span class='muted'>{_esc(k)}</span><b>{_esc(v)}</b></div>" for k, v in items.items()
    ) + "</div>"


def _table(headers: Iterable[str], rows: Iterable[Iterable[str]], empty: str = "No data") -> str:
    headers = list(headers)
    body = "".join("<tr>" + "".join(_cell(c) for c in r) + "</tr>" for r in rows) \
        or f"<tr><td colspan='{len(headers)}'>{empty}</td></tr>"
    return ("<div class='wrap'><table><tr>" + "".join(f"<th>{_esc(h)}</th>" for h in headers)
            + f"</tr>{body}</table></div>")


class Centered(str):
    """Marks a pre-rendered cell as centred (a type, not a magic prefix that could collide with data)."""


def _cell(content: str) -> str:
    return f"<td class='cell'>{content}</td>" if isinstance(content, Centered) else f"<td>{content}</td>"


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_esc(title)}</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>")


def _banner(records: list[dict[str, Any]]) -> str:
    if not any(r["source"] == "mock" for r in records):
        return ""
    return ("<div class='banner'><b>Mock accounts.</b> Results come from the deterministic mock provider "
            "(data/scenario.json) because no platform or Spikerz credentials were supplied. Each day is "
            "recorded on the real calendar date the check ran. See ASSUMPTIONS.md.</div>")


def day_label(store: Store, run_date: str) -> str:
    start = store.monitoring_start()
    return f"Day {clock.day_number(start, date.fromisoformat(run_date))}" if start else run_date


# ---------------------------------------------------------------- outputs
def write_daily(store: Store, run_date: str, out_dir: Path) -> dict[str, Path]:
    records = store.health_checks(run_date=run_date)
    summary = Counter(r["severity"] for r in records)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {ext: out_dir / f"{run_date}.{ext}" for ext in ("json", "csv", "html")}

    paths["json"].write_text(json.dumps({
        "run_date": run_date, "monitoring_day": day_label(store, run_date), "accounts_checked": len(records),
        "summary": {s: summary.get(s, 0) for s in SEVERITIES},
        "mock": any(r["source"] == "mock" for r in records), "results": records}, indent=2))

    with paths["csv"].open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in records:
            writer.writerow({**{k: r[k] for k in CSV_FIELDS},
                             **{k: join_list(r[k]) for k in ("connected_assets", "permissions", "missing_permissions")},
                             "findings": " || ".join(f"[{f['severity']}] {f['message']}" for f in r["findings"])})

    rows = [[r["account_id"], PLATFORM_LABEL[r["platform"]], _pill(r["severity"]), r["account_status"],
             r["connection_status"], _token_cell(r), str(len(r["connected_assets"])),
             _esc(", ".join(r["missing_permissions"]) or "-"),
             "<ul>" + "".join(f"<li>{_esc(f['message'])}</li>" for f in r["findings"]) + "</ul>" if r["findings"] else "-"]
            for r in records]
    body = (f"<h1>{day_label(store, run_date)} health check - {run_date}</h1>"
            f"<p class='muted'>{len(records)} accounts checked</p>{_banner(records)}"
            f"{_cards({s: summary.get(s, 0) for s in SEVERITIES})}<h2>Results</h2>"
            + _table(["Account", "Platform", "Severity", "Status", "Connection", "Token", "Assets",
                      "Missing perms", "Findings"], rows))
    paths["html"].write_text(_page(f"Health check {run_date}", body))
    return paths


def _token_cell(r: dict[str, Any]) -> str:
    state = "-" if r["token_valid"] is None else ("valid" if r["token_valid"] else "INVALID")
    return f"{state}<br><span class='muted nowrap'>{(r['token_expires_at'] or '')[:10]}</span>"


def write_dashboard(store: Store, out_path: Path) -> Path:
    dates, records = store.run_dates(), store.health_checks()
    by_acct: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in records:
        by_acct[r["account_id"]][r["run_date"]] = r
    pending = max(0, MONITORING_TARGET_DAYS - len(dates))
    head = [f"{day_label(store, d)} ({d[5:]})" for d in dates] + [f"Day {len(dates) + i + 1}" for i in range(pending)]
    pending_cells = [Centered(_pill("pending"))] * pending

    platform_rows = []
    for p in PLATFORMS:
        cells = []
        for d in dates:
            day = [r for r in records if r["platform"] == p and r["run_date"] == d]
            healthy = sum(r["severity"] in ("ok", "info") for r in day)
            cells.append(Centered(f"{_pill(worst(r['severity'] for r in day))}<br>"
                                  f"<span class='muted'>{healthy}/{len(day)} healthy</span>" if day else "-"))
        platform_rows.append([f"<b>{PLATFORM_LABEL[p]}</b>", *cells, *pending_cells])

    account_rows = [[_esc(a.account_id), a.label, _esc(a.handle),
                     *[_account_cell(by_acct[a.account_id].get(d)) for d in dates], *pending_cells]
                    for a in store.list_accounts(include_retired=True)]

    issues = store.issues()
    issue_rows = [[i["issue_id"], _pill(i["severity"]), i["status"], i["opened_on"], i["closed_on"] or "-",
                   _esc(i["title"])] for i in issues]

    latest = [r for r in records if dates and r["run_date"] == dates[-1]]
    unknown = sum(r["connection_status"] == "error" and any(f["category"] == "check_failure" for f in r["findings"])
                  for r in latest)
    connected = sum(r["connection_status"] == "connected" for r in latest)
    body = (f"<h1>Spikerz QA - test account health</h1>"
            f"<p class='muted'>Monitoring started {dates[0] if dates else 'not yet'}; last check "
            f"{dates[-1] if dates else '-'}</p>{_banner(records)}"
            + _cards({"Accounts": len(by_acct), "Monitoring progress": f"{len(dates)}/{MONITORING_TARGET_DAYS} days",
                      "Connected (latest)": f"{connected}/{len(latest)}" + (f" ({unknown} unknown)" if unknown else ""),
                      "Issues opened": len(issues), "Issues open": sum(i["status"] == "open" for i in issues)})
            + "<h2>Health by platform and day</h2>" + _table(["Platform", *head], platform_rows)
            + "<h2>Health by account and day</h2>" + _table(["ID", "Platform", "Handle", *head], account_rows)
            + "<h2>Issues</h2>" + _table(["ID", "Severity", "Status", "Opened", "Closed", "Title"], issue_rows,
                                         "No issues"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_page("Spikerz QA dashboard", body))
    return out_path


def _account_cell(record: dict[str, Any] | None) -> str:
    if not record:
        return Centered("-")
    tip = _esc("; ".join(f["message"] for f in record["findings"]))
    return Centered(f"<span title='{tip}'>{_pill(record['severity'])}</span>")


def write_maintenance_log(store: Store, out_path: Path) -> Path:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in store.maintenance_log():
        by_day[e["log_date"]].append(e)
    run_dates = set(store.run_dates())
    lines = ["# Five-day maintenance log", "",
             "> Mock accounts (no credentials were supplied). Every entry is written on the real date the "
             "event happened; earlier days are never rewritten. Scripted mock-operator actions are labelled.", ""]
    for day, items in sorted(by_day.items()):
        heading = f"{day_label(store, day)} - {day}" if day in run_dates else f"Setup / onboarding - {day}"
        lines += [f"## {heading}", "", "| Time (UTC) | Account | Event | Detail | Actor |", "|---|---|---|---|---|"]
        lines += [f"| {e['ts'][11:16]} | {e['account_id'] or '-'} | `{e['event_type']}` | "
                  f"{e['description'].replace('|', '/')} | {e['actor']} |" for e in items]
        lines.append("")
    remaining = MONITORING_TARGET_DAYS - len(run_dates)
    if remaining > 0:
        lines += [f"_{remaining} monitoring day(s) still to run; the daily job appends them here._", ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    return out_path


def write_issue_reports(store: Store, out_path: Path) -> Path:
    issues = store.issues()
    lines = ["# Issue log / bug reports", "", "Auto-generated by `spikerz-qa report`. One section per tracked issue.", "",
             "| ID | Severity | Status | Account | Opened | Closed | Title |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {i['issue_id']} | {i['severity']} | {i['status']} | {i['account_id']} | {i['opened_on']} | "
              f"{i['closed_on'] or '-'} | {i['title']} |" for i in issues]
    if not issues:
        lines.append("| - | - | - | - | - | - | No issues detected yet |")
    for i in issues:
        closed = f" (closed {i['closed_on']})" if i["closed_on"] else ""
        lines += ["", f"## {i['issue_id']}: {i['title']}", "",
                  f"- **Severity:** {i['severity']}", f"- **Status:** {i['status']}{closed}",
                  f"- **Category:** {i['category']}", f"- **Opened:** {i['opened_on']}", "",
                  "**Reproduction steps**", "", i["repro_steps"], "",
                  "**Evidence**", "", "```json", json.dumps(i["evidence"], indent=2), "```", "",
                  f"**Likely root cause:** {i['root_cause']}", "",
                  f"**Recommended next action:** {i['next_action']}"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    return out_path


def export_inventory(store: Store, out_dir: Path) -> dict[str, Path]:
    accounts = [a.to_dict() for a in store.list_accounts(include_retired=True)]
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "inventory_export.json", "csv": out_dir / "inventory_export.csv",
             "audit": out_dir / "audit_log.json"}
    paths["json"].write_text(json.dumps(accounts, indent=2))
    with paths["csv"].open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(accounts[0]) if accounts else ["account_id"])
        writer.writeheader()
        writer.writerows({k: join_list(v) if isinstance(v, list) else v for k, v in a.items()} for a in accounts)
    paths["audit"].write_text(json.dumps(store.audit_log(), indent=2))
    return paths


def write_all(store: Store, reports: Path) -> list[Path]:
    """Rebuild every output from the database (idempotent)."""
    out = [p for d in store.run_dates() for p in write_daily(store, d, reports / "daily").values()]
    out += [write_dashboard(store, reports / "dashboard.html"),
            write_maintenance_log(store, reports / "maintenance_log.md"),
            write_issue_reports(store, reports / "issues.md")]
    out += export_inventory(store, reports / "export").values()
    return out
