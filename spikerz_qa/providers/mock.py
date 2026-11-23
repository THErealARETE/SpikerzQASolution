"""Deterministic, scenario-driven mock platform.

The scenario (``data/scenario.json``) is written in *monitoring days*, not dates:
Day 1 is whatever date the first real health check ran. State for Day N is the
baseline with every event for days <= N applied in order, so the same day always
yields the same answer and nothing is ever generated ahead of time.

Event keys (all optional):
  set                                   : fields to overwrite (account_status, connection_status,
                                          token_expires_in_days, error, setup_complete,
                                          stale_token_metadata)
  add_permissions / remove_permissions  : list[str]
  add_assets / remove_assets            : list[str]
  transient_failures                    : int - fail the first N attempts *that day only*
  transient_error                       : str - message for those failures (default HTTP 429)
"""
from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .. import clock, config
from ..models import Account
from . import Provider, ProviderResult, TransientProviderError


class MockProvider(Provider):
    name = "mock"

    def __init__(self, monitoring_start: date, scenario_path: str | Path | None = None):
        self.scenario: dict[str, Any] = json.loads(Path(scenario_path or config.scenario_path()).read_text())
        self.start = monitoring_start
        self._attempts: dict[tuple[str, date], int] = {}

    # ---------------------------------------------------------------- helpers
    def _day(self, as_of: date) -> int:
        return clock.day_number(self.start, as_of)

    def _events(self, account_id: str, up_to_day: int, exact: bool = False) -> list[dict[str, Any]]:
        return [e for e in self.scenario["events"] if e["account_id"] == account_id
                and (e["day"] == up_to_day if exact else e["day"] <= up_to_day)]

    def state_for(self, account: Account, as_of: date) -> dict[str, Any]:
        day = self._day(as_of)
        state = {
            "account_status": "active",
            "connection_status": "connected",
            "token_expires_in_days": self.scenario["default_token_ttl_days"].get(account.platform, 60),
            "token_set_on_day": 1,
            "permissions": list(account.required_permissions),
            "assets": list(account.connected_assets),
            "error": None,
            "setup_complete": True,
            # Source keeps reporting 'connected' after the recorded token expiry
            # (metadata out of sync with the real connection state).
            "stale_token_metadata": False,
            **copy.deepcopy(self.scenario["baseline"].get(account.account_id, {})),
        }
        for event in self._events(account.account_id, day):
            changes = event.get("set", {})
            state.update(changes)
            if "token_expires_in_days" in changes:
                state["token_set_on_day"] = event["day"]
            for kind in ("permissions", "assets"):
                state[kind] = [x for x in state[kind] if x not in event.get(f"remove_{kind}", [])]
                state[kind] += [x for x in event.get(f"add_{kind}", []) if x not in state[kind]]
        return state

    # ------------------------------------------------------------- interface
    def check(self, account: Account, as_of: date) -> ProviderResult:
        key = (account.account_id, as_of)
        self._attempts[key] = self._attempts.get(key, 0) + 1
        flaky = [e for e in self._events(account.account_id, self._day(as_of), exact=True)
                 if e.get("transient_failures")]
        if self._attempts[key] <= sum(e["transient_failures"] for e in flaky):
            raise TransientProviderError(f"{flaky[0].get('transient_error', 'HTTP 429 Too Many Requests')} (simulated)")

        state = self.state_for(account, as_of)
        token_issued = clock.start_of_day(self.start + timedelta(days=state["token_set_on_day"] - 1))
        expires = token_issued + timedelta(days=state["token_expires_in_days"])
        if state["stale_token_metadata"]:
            expires = clock.start_of_day(as_of) - timedelta(days=30)
        token_valid = (state["connection_status"] not in ("expired", "disconnected")
                       and expires > clock.start_of_day(as_of))
        connection = state["connection_status"]
        if not token_valid and connection == "connected" and not state["stale_token_metadata"]:
            connection = "expired"
        if state["stale_token_metadata"]:
            token_valid = None  # the source's token record can't be trusted
        return ProviderResult(
            account_status=state["account_status"],
            connection_status=connection,
            token_valid=token_valid,
            token_expires_at=clock.iso(expires),
            connected_assets=sorted(state["assets"]),
            permissions=sorted(state["permissions"]),
            errors=[state["error"]] if state["error"] else [],
            source=self.name,
            setup_complete=state["setup_complete"],
        )

    def scripted_actions(self, as_of: date) -> list[dict[str, Any]]:
        return [a for a in self.scenario.get("operator_actions", []) if a["day"] == self._day(as_of)]
