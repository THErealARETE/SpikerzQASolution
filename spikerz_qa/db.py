"""SQLite persistence: accounts, health checks, issues, audit trail, maintenance log.

Generic helpers (_insert, _select, _encode/_decode) keep SQL and JSON handling in
one place; the Store methods only describe *what* is stored.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import clock
from .models import LIST_FIELDS, MUTABLE_FIELDS, Account, ValidationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id        TEXT PRIMARY KEY,
    platform          TEXT NOT NULL,
    account_type      TEXT NOT NULL,
    environment       TEXT NOT NULL,
    owner_team        TEXT NOT NULL,
    handle            TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL,
    connection_status TEXT NOT NULL,
    connected_assets  TEXT NOT NULL DEFAULT '[]',
    permissions       TEXT NOT NULL DEFAULT '[]',
    last_health_check TEXT,
    notes             TEXT NOT NULL DEFAULT '',
    credential_ref    TEXT NOT NULL,
    provider          TEXT NOT NULL DEFAULT 'mock',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS health_checks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL,
    run_date            TEXT NOT NULL,
    checked_at          TEXT NOT NULL,
    account_id          TEXT NOT NULL REFERENCES accounts(account_id),
    platform            TEXT NOT NULL,
    account_status      TEXT NOT NULL,
    connection_status   TEXT NOT NULL,
    token_valid         INTEGER,
    token_expires_at    TEXT,
    connected_assets    TEXT NOT NULL,
    permissions         TEXT NOT NULL,
    missing_permissions TEXT NOT NULL,
    severity            TEXT NOT NULL,
    findings            TEXT NOT NULL,
    attempts            INTEGER NOT NULL,
    source              TEXT NOT NULL,
    UNIQUE(run_date, account_id)
);
CREATE TABLE IF NOT EXISTS issues (
    issue_id    TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES accounts(account_id),
    opened_on   TEXT NOT NULL,
    closed_on   TEXT,
    status      TEXT NOT NULL,
    severity    TEXT NOT NULL,
    category    TEXT NOT NULL,
    title       TEXT NOT NULL,
    repro_steps TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    root_cause  TEXT NOT NULL,
    next_action TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    account_id TEXT NOT NULL,
    action     TEXT NOT NULL,
    changes    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS maintenance_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date    TEXT NOT NULL,
    ts          TEXT NOT NULL,
    account_id  TEXT,
    event_type  TEXT NOT NULL,
    description TEXT NOT NULL,
    actor       TEXT NOT NULL
);
"""

# JSON-encoded columns per table.
JSON_COLUMNS = {
    "accounts": LIST_FIELDS,
    "health_checks": ("connected_assets", "permissions", "missing_permissions", "findings"),
    "issues": ("evidence",),
    "audit_log": ("changes",),
    "maintenance_log": (),
}
BOOL_COLUMNS = {"health_checks": ("token_valid",)}


def _encode(table: str, row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for col in JSON_COLUMNS[table]:
        if col in out:
            out[col] = json.dumps(out[col], default=str)
    for col in BOOL_COLUMNS.get(table, ()):
        if col in out and out[col] is not None:
            out[col] = int(out[col])
    return out


def _decode(table: str, row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for col in JSON_COLUMNS[table]:
        out[col] = json.loads(out[col])
    for col in BOOL_COLUMNS.get(table, ()):
        if out[col] is not None:
            out[col] = bool(out[col])
    return out


def _where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    """Equality filters; None values are ignored."""
    active = {k: v for k, v in filters.items() if v is not None}
    if not active:
        return "", []
    return "WHERE " + " AND ".join(f"{k} = ?" for k in active), list(active.values())


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------ generic SQL
    def _insert(self, table: str, row: dict[str, Any], replace: bool = False) -> None:
        row = _encode(table, row)
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        sql = f"{verb} INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})"
        self.conn.execute(sql, list(row.values()))

    def _select(self, table: str, order_by: str, extra_where: str = "",
                extra_params: Iterable[Any] = (), **filters: Any) -> list[dict[str, Any]]:
        where, params = _where(filters)
        params += list(extra_params)
        if extra_where:
            where = f"{where} AND {extra_where}" if where else f"WHERE {extra_where}"
        rows = self.conn.execute(f"SELECT * FROM {table} {where} ORDER BY {order_by}", params)
        return [_decode(table, r) for r in rows]

    # -------------------------------------------------------------- accounts
    def add_account(self, account: Account, actor: str) -> Account:
        account.validate()
        ts = clock.iso(clock.now())
        account.created_at = account.created_at or ts
        account.updated_at = ts
        with self.tx():
            try:
                self._insert("accounts", account.to_dict())
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"{account.account_id}: already exists") from exc
            self._audit(ts, actor, account.account_id, "create", account.to_dict())
        return account

    def upsert_account(self, account: Account, actor: str) -> str:
        if self.get_account(account.account_id):
            self.update_account(account.account_id,
                                {k: getattr(account, k) for k in MUTABLE_FIELDS}, actor)
            return "updated"
        self.add_account(account, actor)
        return "created"

    def get_account(self, account_id: str) -> Account | None:
        rows = self._select("accounts", "account_id", account_id=account_id)
        return Account(**rows[0]) if rows else None

    def list_accounts(self, include_retired: bool = False, **filters: str | None) -> list[Account]:
        hide_retired = "status != 'retired'" if not include_retired and filters.get("status") != "retired" else ""
        rows = self._select("accounts", "platform, account_id", hide_retired, **filters)
        return [Account(**r) for r in rows]

    def update_account(self, account_id: str, changes: dict[str, Any], actor: str,
                       ts: str | None = None, action: str = "update") -> dict[str, Any]:
        current = self.get_account(account_id)
        if not current:
            raise ValidationError(f"{account_id}: not found")
        unknown = set(changes) - set(MUTABLE_FIELDS) - {"last_health_check"}
        if unknown:
            raise ValidationError(f"{account_id}: cannot update {sorted(unknown)}")
        diff = {k: {"from": getattr(current, k), "to": v}
                for k, v in changes.items() if not _same(getattr(current, k), v)}
        if not diff:
            return {}
        for key, change in diff.items():
            setattr(current, key, change["to"])
        current.validate()
        current.updated_at = ts or clock.iso(clock.now())
        row = _encode("accounts", current.to_dict())
        row.pop("account_id")
        with self.tx() as c:
            c.execute(f"UPDATE accounts SET {', '.join(f'{k} = ?' for k in row)} WHERE account_id = ?",
                      [*row.values(), account_id])
            operator_visible = {k: v for k, v in diff.items() if k != "last_health_check"}
            if operator_visible:
                self._audit(current.updated_at, actor, account_id, action, operator_visible)
        return diff

    # --------------------------------------------------------- health checks
    def save_health_check(self, record: dict[str, Any]) -> None:
        with self.tx():
            # One row per account per day: re-running today replaces today only.
            self._insert("health_checks", record, replace=True)

    def health_checks(self, run_date: str | None = None, account_id: str | None = None
                      ) -> list[dict[str, Any]]:
        return self._select("health_checks", "run_date, platform, account_id",
                            run_date=run_date, account_id=account_id)

    def previous_check(self, account_id: str, before_date: str) -> dict[str, Any] | None:
        rows = self._select("health_checks", "run_date DESC", "run_date < ?", [before_date],
                            account_id=account_id)
        return rows[0] if rows else None

    def run_dates(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT run_date FROM health_checks ORDER BY run_date")]

    def monitoring_start(self) -> date | None:
        dates = self.run_dates()
        return date.fromisoformat(dates[0]) if dates else None

    # ---------------------------------------------------------------- issues
    def open_issue(self, issue: dict[str, Any]) -> tuple[str, bool]:
        """Return (issue_id, created). Deduplicated per account + category while open."""
        existing = self._select("issues", "issue_id", account_id=issue["account_id"],
                                category=issue["category"], status="open")
        if existing:
            return existing[0]["issue_id"], False
        count = self.conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        issue_id = f"QA-{count + 1:03d}"
        with self.tx():
            self._insert("issues", {"issue_id": issue_id, "status": "open", "closed_on": None, **issue})
        return issue_id, True

    def close_issues(self, account_id: str, closed_on: str, categories: Iterable[str] | None = None
                     ) -> list[str]:
        open_issues = self._select("issues", "issue_id", account_id=account_id, status="open")
        allowed = set(categories) if categories is not None else None
        ids = [i["issue_id"] for i in open_issues if allowed is None or i["category"] in allowed]
        with self.tx() as c:
            c.executemany("UPDATE issues SET status = 'resolved', closed_on = ? WHERE issue_id = ?",
                          [(closed_on, i) for i in ids])
        return ids

    def issues(self, status: str | None = None) -> list[dict[str, Any]]:
        return self._select("issues", "issue_id", status=status)

    # ------------------------------------------------ audit & maintenance log
    def _audit(self, ts: str, actor: str, account_id: str, action: str, changes: dict[str, Any]) -> None:
        self._insert("audit_log", {"ts": ts, "actor": actor, "account_id": account_id,
                                   "action": action, "changes": changes})

    def audit_log(self, account_id: str | None = None) -> list[dict[str, Any]]:
        return self._select("audit_log", "id", account_id=account_id)

    def log_maintenance(self, log_date: str, event_type: str, description: str, actor: str,
                        account_id: str | None = None, ts: str | None = None) -> None:
        with self.tx():
            self._insert("maintenance_log", {
                "log_date": log_date, "ts": ts or clock.iso(clock.now()), "account_id": account_id,
                "event_type": event_type, "description": description, "actor": actor})

    def maintenance_log(self) -> list[dict[str, Any]]:
        return self._select("maintenance_log", "id")


def _same(old: Any, new: Any) -> bool:
    """Lists are sets semantically: re-ordering is not a change."""
    if isinstance(old, list) and isinstance(new, list):
        return sorted(old) == sorted(new)
    return old == new
