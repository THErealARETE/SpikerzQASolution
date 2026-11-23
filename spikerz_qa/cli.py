"""Command-line interface: `python -m spikerz_qa <command>` (or `spikerz-qa` once installed).

Handlers are thin: parse args -> call the domain layer -> print. No business logic here.
"""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from . import clock, config, reporting
from .db import Store
from .healthcheck import run_daily
from .models import (ACCOUNT_STATUSES, CONNECTION_STATUSES, ENVIRONMENTS, LIST_FIELDS, MIN_PER_PLATFORM,
                     MUTABLE_FIELDS, PLATFORMS, PROVIDERS, SEVERITIES, Account, ValidationError, at_least, split_list)
from .providers import AccountRouter, build_router


# ------------------------------------------------------------------ helpers
def actor_name(args: argparse.Namespace) -> str:
    return args.actor or os.environ.get("SPIKERZ_QA_ACTOR") or getpass.getuser()


def today_iso() -> str:
    return clock.today().isoformat()


def load_records(path: Path) -> list[dict[str, Any]]:
    loaders: dict[str, Callable[[], Any]] = {
        ".json": lambda: json.loads(path.read_text()),
        ".csv": lambda: list(csv.DictReader(path.read_text().splitlines())),
    }
    if path.suffix.lower() not in loaders:
        raise ValidationError("import file must be .json or .csv")
    data = loaders[path.suffix.lower()]()
    return data["accounts"] if isinstance(data, dict) else data


def check_distribution(accounts: list[Account]) -> list[str]:
    counts = Counter(a.platform for a in accounts if a.status != "retired")
    return [f"{p}: {counts.get(p, 0)} active accounts (< {MIN_PER_PLATFORM} required)"
            for p in PLATFORMS if counts.get(p, 0) < MIN_PER_PLATFORM]


def warn_distribution(store: Store) -> None:
    for warning in check_distribution(store.list_accounts()):
        print(f"  WARNING distribution {warning}", file=sys.stderr)


def build_provider(name: str, store: Store) -> AccountRouter:
    """'auto' routes each account to its own provider; a name restricts the run to that source.
    Day 1 = first day monitoring actually ran (or today on the first run)."""
    return build_router(store.monitoring_start() or clock.today(), None if name == "auto" else name)


def print_table(accounts: list[Account]) -> None:
    cols = ("account_id", "platform", "account_type", "environment", "owner_team", "status",
            "connection_status", "last_health_check")
    rows = [[str(getattr(a, c) or "-") for c in cols] for a in accounts]
    widths = [max([len(c)] + [len(r[i]) for r in rows]) for i, c in enumerate(cols)]
    for line in [list(cols), *rows]:
        print("  ".join(v.ljust(w) for v, w in zip(line, widths)))
    print(f"\n{len(accounts)} account(s)")


def print_summary(records: list[dict[str, Any]]) -> None:
    counts = Counter(r["severity"] for r in records)
    print(f"{len(records)} checked: " + ", ".join(f"{s}={counts[s]}" for s in SEVERITIES if counts.get(s)))
    for r in records:
        for f in r["findings"]:
            print(f"  {r['account_id']} [{f['severity']}] {f['message']}")


# ----------------------------------------------------------------- commands
def cmd_init(args, store: Store) -> int:
    print(f"Database ready at {store.path}")
    return 0


def cmd_import(args, store: Store) -> int:
    created = updated = 0
    errors = []
    for rec in load_records(Path(args.file)):
        try:
            outcome = store.upsert_account(Account.from_dict(rec), actor_name(args))
        except (ValidationError, TypeError, KeyError) as exc:
            errors.append(str(exc))
            continue
        if outcome == "created":
            created += 1
            store.log_maintenance(today_iso(), "account_onboarded",
                                  f"Added to inventory. {rec.get('notes', '')}".strip(), actor_name(args),
                                  rec["account_id"])
        else:
            updated += 1
    print(f"Imported: {created} created, {updated} updated, {len(errors)} rejected")
    for e in errors:
        print(f"  REJECTED {e}", file=sys.stderr)
    warn_distribution(store)
    return 1 if errors else 0


def cmd_list(args, store: Store) -> int:
    accounts = store.list_accounts(include_retired=args.all, platform=args.platform, status=args.status,
                                   connection_status=args.connection, environment=args.environment,
                                   owner_team=args.team)
    if args.json:
        print(json.dumps([a.to_dict() for a in accounts], indent=2))
    else:
        print_table(accounts)
    return 0


def cmd_show(args, store: Store) -> int:
    acct = store.get_account(args.account_id)
    if not acct:
        raise ValidationError(f"{args.account_id}: not found")
    print(json.dumps({"account": acct.to_dict(),
                      "recent_checks": store.health_checks(account_id=args.account_id)[-5:],
                      "audit": store.audit_log(args.account_id)}, indent=2, default=str))
    return 0


def cmd_add(args, store: Store) -> int:
    acct = Account(account_id=args.account_id, platform=args.platform, account_type=args.account_type,
                   environment=args.environment, owner_team=args.team, handle=args.handle or "",
                   credential_ref=args.credential_ref, notes=args.notes or "",
                   connected_assets=args.assets or [], permissions=args.permissions or [])
    store.add_account(acct, actor_name(args))
    store.log_maintenance(today_iso(), "account_onboarded", "Added to inventory (pending connection)",
                          actor_name(args), acct.account_id)
    print(f"Added {acct.account_id}")
    return 0


def cmd_update(args, store: Store) -> int:
    changes = {}
    for item in args.set:
        key, _, value = item.partition("=")
        if key not in MUTABLE_FIELDS:
            raise ValidationError(f"'{key}' is not updatable; allowed: {', '.join(MUTABLE_FIELDS)}")
        changes[key] = split_list(value) if key in LIST_FIELDS else value
    diff = store.update_account(args.account_id, changes, actor_name(args))
    print(json.dumps(diff, indent=2) if diff else "No changes")
    if diff and args.reason:
        store.log_maintenance(today_iso(), "manual_update", f"{args.reason}: {sorted(diff)}",
                              actor_name(args), args.account_id)
    return 0


def cmd_retire(args, store: Store) -> int:
    """Cleanup workflow: soft delete (history kept), close issues, print revocation checklist."""
    diff = store.update_account(args.account_id, {"status": "retired", "connection_status": "disconnected",
                                                  "notes": f"RETIRED: {args.reason}"},
                                actor_name(args), action="retire")
    store.close_issues(args.account_id, today_iso())
    store.log_maintenance(today_iso(), "account_retired", args.reason, actor_name(args), args.account_id)
    print(f"Retired {args.account_id}. Checklist: disconnect in Spikerz UI, revoke app access on the "
          f"platform, delete the secret behind its credential_ref, provision a replacement.")
    warn_distribution(store)
    return 0 if diff else 1


def cmd_check(args, store: Store) -> int:
    """The daily job: check every account for TODAY, then rebuild all reports."""
    records = run_daily(store, build_provider(args.provider, store), actor="healthcheck-bot",
                        account_id=args.account)
    print(f"{reporting.day_label(store, today_iso())} ({today_iso()})")
    print_summary(records)
    reporting.write_all(store, config.reports_dir())
    print(f"Dashboard: {config.reports_dir() / 'dashboard.html'}")
    failed = any(at_least(r["severity"], "error") for r in records)
    return 2 if failed and args.fail_on_error else 0


def cmd_report(args, store: Store) -> int:
    for path in reporting.write_all(store, config.reports_dir()):
        print(path)
    return 0


def cmd_issues(args, store: Store) -> int:
    for i in store.issues(args.status):
        print(f"{i['issue_id']}  {i['severity']:<8} {i['status']:<8} {i['opened_on']}  {i['title']}")
    return 0


def cmd_audit(args, store: Store) -> int:
    for e in store.audit_log(args.account_id):
        print(f"{e['ts']}  {e['actor']:<16} {e['account_id']}  {e['action']:<16} {json.dumps(e['changes'])}")
    return 0


def cmd_log(args, store: Store) -> int:
    store.log_maintenance(today_iso(), args.type, args.message, actor_name(args), args.account)
    print("Logged")
    return 0


# ------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="spikerz-qa", description="Spikerz QA test-account manager")
    p.add_argument("--db", default=None, help="SQLite path (env SPIKERZ_QA_DB)")
    p.add_argument("--actor", help="who is making the change (audit trail); env SPIKERZ_QA_ACTOR")
    sub = p.add_subparsers(dest="command", required=True)

    def command(name: str, fn: Callable, help_text: str) -> argparse.ArgumentParser:
        s = sub.add_parser(name, help=help_text)
        s.set_defaults(fn=fn)
        return s

    command("init", cmd_init, "create the database")
    command("import", cmd_import, "import/upsert accounts from CSV or JSON").add_argument("file")

    s = command("list", cmd_list, "view and filter accounts")
    s.add_argument("--platform", choices=PLATFORMS)
    s.add_argument("--status", choices=ACCOUNT_STATUSES)
    s.add_argument("--connection", choices=CONNECTION_STATUSES)
    s.add_argument("--environment", choices=ENVIRONMENTS)
    s.add_argument("--team")
    s.add_argument("--all", action="store_true", help="include retired accounts")
    s.add_argument("--json", action="store_true")

    command("show", cmd_show, "one account with recent checks and audit trail").add_argument("account_id")

    s = command("add", cmd_add, "add an account")
    s.add_argument("account_id")
    s.add_argument("--platform", required=True, choices=PLATFORMS)
    s.add_argument("--account-type", required=True)
    s.add_argument("--environment", default="mock", choices=ENVIRONMENTS)
    s.add_argument("--team", required=True)
    s.add_argument("--credential-ref", required=True, help="e.g. env:SPIKERZ_QA_LI_004_TOKEN")
    s.add_argument("--handle")
    s.add_argument("--notes")
    s.add_argument("--assets", nargs="*")
    s.add_argument("--permissions", nargs="*")

    s = command("update", cmd_update, "update fields: --set key=value (lists use '|')")
    s.add_argument("account_id")
    s.add_argument("--set", action="append", required=True, metavar="KEY=VALUE")
    s.add_argument("--reason", help="also record in the maintenance log")

    s = command("retire", cmd_retire, "retire an account (soft delete + checklist)")
    s.add_argument("account_id")
    s.add_argument("--reason", required=True)

    s = command("check", cmd_check, "run today's health check and rebuild reports")
    s.add_argument("--provider", choices=("auto", *PROVIDERS), default=config.default_provider(),
                   help="auto (default): each account uses its own provider")
    s.add_argument("--account", help="only this account")
    s.add_argument("--fail-on-error", action="store_true", help="exit 2 when any error/critical found (CI)")

    command("report", cmd_report, "rebuild dashboard, daily reports, logs, issues, export")
    command("issues", cmd_issues, "list tracked issues").add_argument("--status", choices=("open", "resolved"))
    command("audit", cmd_audit, "show the audit trail").add_argument("account_id", nargs="?")

    s = command("log", cmd_log, "add a maintenance-log entry for today (e.g. corrective action)")
    s.add_argument("message")
    s.add_argument("--type", default="corrective_action")
    s.add_argument("--account")
    return p


def main(argv: list[str] | None = None) -> int:
    config.load_dotenv()
    args = build_parser().parse_args(argv)
    store = Store(args.db or config.db_path())
    try:
        return args.fn(args, store)
    except ValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
