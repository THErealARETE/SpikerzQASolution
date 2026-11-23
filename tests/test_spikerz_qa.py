"""Unit + integration tests. Multi-day behaviour is exercised in an in-memory DB with
fixed past dates; the CLI itself can only ever record checks for today."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from spikerz_qa import clock, credentials, reporting
from spikerz_qa.cli import build_parser, check_distribution, load_records, main
from spikerz_qa.db import Store
from spikerz_qa.healthcheck import evaluate, run_daily
from spikerz_qa.models import REQUIRED_PERMISSIONS, Account, ValidationError
from spikerz_qa import http
from spikerz_qa.providers import AccountRouter, PermanentProviderError, ProviderResult, TransientProviderError
from spikerz_qa.providers.live import LiveProvider
from spikerz_qa.providers.mock import MockProvider
from spikerz_qa.retry import with_retry

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "data" / "accounts.json"
SCENARIO = ROOT / "data" / "scenario.json"
START = date(2026, 1, 5)


def at(day: int, hour: int = 9) -> datetime:
    """Monitoring Day N at HH:00 UTC."""
    return datetime(START.year, START.month, START.day, hour, tzinfo=timezone.utc) + timedelta(days=day - 1)


def make_account(**overrides) -> Account:
    base = dict(account_id="FB-901", platform="facebook", account_type="page", environment="mock",
                owner_team="qa", credential_ref="mock:fb/901", connected_assets=["page:1"],
                permissions=list(REQUIRED_PERMISSIONS["facebook"]), connection_status="connected")
    return Account(**{**base, **overrides})


class TempEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SPIKERZ_QA_REPORTS": self.tmp.name,
                                                "SPIKERZ_QA_ALERTS_FILE": str(Path(self.tmp.name) / "alerts.jsonl")})
        self.env.start()
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()
        self.env.stop()
        self.tmp.cleanup()

    def load_inventory(self):
        for rec in load_records(INVENTORY):
            self.store.add_account(Account.from_dict(rec), "test")

    def run_days(self, n=5):
        self.load_inventory()
        provider = MockProvider(START, SCENARIO)
        return [{r["account_id"]: r for r in run_daily(self.store, provider, now=at(d), sleep=lambda s: None)}
                for d in range(1, n + 1)]


class TestModel(unittest.TestCase):
    def test_valid_account(self):
        make_account().validate()

    def test_rejects_raw_secret_in_credential_ref(self):
        with self.assertRaises(ValidationError):
            make_account(credential_ref="EAAGm0PX4ZCpsBA...rawtoken").validate()

    def test_rejects_bad_enum_and_id(self):
        with self.assertRaises(ValidationError) as ctx:
            make_account(account_id="fb1", platform="myspace").validate()
        self.assertIn("account_id", str(ctx.exception))
        self.assertIn("platform", str(ctx.exception))

    def test_csv_pipe_lists(self):
        acct = Account.from_dict({**make_account().to_dict(), "connected_assets": "page:1|page:2"})
        self.assertEqual(acct.connected_assets, ["page:1", "page:2"])


class TestInventory(TempEnv):
    def test_import_has_15_accounts_and_meets_distribution(self):
        self.load_inventory()
        accounts = self.store.list_accounts()
        self.assertEqual(len(accounts), 15)
        self.assertEqual(check_distribution(accounts), [])

    def test_filter_and_update_writes_audit(self):
        self.load_inventory()
        self.assertEqual(len(self.store.list_accounts(platform="tiktok")), 3)
        diff = self.store.update_account("TT-001", {"notes": "hello"}, "alice")
        self.assertEqual(diff["notes"]["to"], "hello")
        audit = self.store.audit_log("TT-001")
        self.assertEqual([a["action"] for a in audit], ["create", "update"])
        self.assertEqual(audit[-1]["actor"], "alice")

    def test_reordered_list_is_not_a_change(self):
        self.store.add_account(make_account(connected_assets=["a", "b"]), "t")
        self.assertEqual(self.store.update_account("FB-901", {"connected_assets": ["b", "a"]}, "t"), {})

    def test_duplicate_rejected(self):
        self.store.add_account(make_account(), "t")
        with self.assertRaises(ValidationError):
            self.store.add_account(make_account(), "t")

    def test_retired_hidden_and_distribution_warns(self):
        self.load_inventory()
        self.store.update_account("XX-001", {"status": "retired"}, "t")
        self.assertEqual(len(self.store.list_accounts(platform="x")), 2)
        self.assertEqual(len(self.store.list_accounts(include_retired=True, platform="x")), 3)
        self.assertTrue(check_distribution(self.store.list_accounts()))

    def test_db_never_contains_secret(self):
        with mock.patch.dict(os.environ, {"SPIKERZ_QA_TEST_TOKEN": "super-secret-value-123456"}):
            self.store.add_account(make_account(credential_ref="env:SPIKERZ_QA_TEST_TOKEN"), "t")
            self.assertNotIn("super-secret-value-123456", "\n".join(self.store.conn.iterdump()))
            self.assertEqual(credentials.resolve("env:SPIKERZ_QA_TEST_TOKEN"), "super-secret-value-123456")


class TestCredentials(unittest.TestCase):
    def test_missing_env_raises(self):
        with self.assertRaises(credentials.CredentialError):
            credentials.resolve("env:DEFINITELY_NOT_SET_12345")

    def test_redact(self):
        out = credentials.redact("call failed access_token=EAAGm0PX4ZCpsBAabcdef123456")
        self.assertNotIn("EAAGm0PX4ZCpsBA", out)
        self.assertIn("REDACTED", out)


class TestRetry(unittest.TestCase):
    def test_recovers(self):
        calls = iter([TransientProviderError("429"), TransientProviderError("429"), "ok"])

        def flaky():
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value
        self.assertEqual(with_retry(flaky, sleep=lambda s: None)[:2], ("ok", 3))

    def test_gives_up_with_exponential_backoff(self):
        delays = []

        def always():
            raise TransientProviderError("503")
        with self.assertRaises(TransientProviderError):
            with_retry(always, attempts=3, base_delay=1, sleep=delays.append)
        self.assertEqual(delays, [1, 2])


class TestEvaluate(unittest.TestCase):
    def evaluate(self, previous=None, **kw):
        acct = make_account()
        base = dict(account_status="active", connection_status="connected", token_valid=True,
                    token_expires_at="2026-03-30T00:00:00+00:00", connected_assets=["page:1"],
                    permissions=acct.required_permissions, errors=[], source="mock")
        return evaluate(acct, ProviderResult(**{**base, **kw}), at(1), previous)

    def test_healthy(self):
        ev = self.evaluate()
        self.assertEqual((ev.severity, ev.findings, ev.missing_permissions), ("ok", [], []))

    def test_missing_permission_is_error(self):
        ev = self.evaluate(permissions=["pages_show_list"])
        self.assertEqual(ev.severity, "error")
        self.assertIn("pages_manage_engagement", ev.missing_permissions)

    def test_unknown_permissions_not_flagged(self):
        self.assertEqual(self.evaluate(permissions=None).severity, "ok")

    def test_token_expiring_is_warning(self):
        ev = self.evaluate(token_expires_at=clock.iso(at(5)))
        self.assertEqual((ev.severity, ev.findings[0].category), ("warning", "token_expiring"))

    def test_locked_is_critical(self):
        self.assertEqual(self.evaluate(account_status="locked").severity, "critical")

    def test_asset_loss_detected(self):
        prev = {"permissions": REQUIRED_PERMISSIONS["facebook"], "connected_assets": ["page:1", "page:2"]}
        ev = self.evaluate(previous=prev)
        self.assertIn(("warning", "assets"), [(f.severity, f.category) for f in ev.findings])


class TestForwardOnlyMonitoring(TempEnv):
    def test_cli_has_no_backdating_option(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["check", "--as-of", "2026-01-01"])

    def test_next_day_appends_and_keeps_previous_days(self):
        self.load_inventory()
        provider = MockProvider(START, SCENARIO)
        run_daily(self.store, provider, now=at(1), sleep=lambda s: None)
        day1 = self.store.health_checks(run_date=START.isoformat())
        run_daily(self.store, provider, now=at(2), sleep=lambda s: None)
        self.assertEqual(self.store.health_checks(run_date=START.isoformat()), day1)
        self.assertEqual(len(self.store.run_dates()), 2)

    def test_rerun_same_day_replaces_only_today(self):
        self.load_inventory()
        provider = MockProvider(START, SCENARIO)
        for hour in (9, 15):
            run_daily(self.store, provider, now=at(1, hour), sleep=lambda s: None)
        self.assertEqual(len(self.store.health_checks(run_date=START.isoformat())), 15)

    def test_day_one_is_first_real_run(self):
        self.run_days(2)
        self.assertEqual(self.store.monitoring_start(), START)
        self.assertEqual(reporting.day_label(self.store, (START + timedelta(days=1)).isoformat()), "Day 2")


class TestFiveDayScenario(TempEnv):
    def test_each_account_checked_once_per_day(self):
        days = self.run_days()
        self.assertEqual([len(d) for d in days], [15] * 5)

    def test_scenario_outcomes(self):
        d1, d2, d3, d4, d5 = self.run_days()
        self.assertEqual(d1["XX-002"]["attempts"], 3)
        self.assertEqual(d2["TT-003"]["connection_status"], "expired")
        self.assertEqual(d3["TT-003"]["connection_status"], "connected")
        self.assertEqual(d2["IG-003"]["missing_permissions"], ["instagram_business_manage_comments"])
        self.assertEqual(d4["IG-003"]["missing_permissions"], [])
        self.assertEqual(d2["LI-002"]["severity"], "ok")
        self.assertEqual(d3["LI-002"]["severity"], "warning")
        self.assertEqual(d4["XX-003"]["severity"], "critical")
        self.assertEqual(d5["XX-003"]["connection_status"], "expired")
        self.assertEqual(d5["LI-002"]["findings"][0]["category"], "check_failure")
        self.assertEqual(d3["LI-003"]["severity"], "warning")  # admin role (asset) lost
        self.assertEqual(d4["LI-003"]["severity"], "info")     # asset restored

    def test_issue_lifecycle(self):
        self.run_days()
        issues = {(i["account_id"], i["category"]): i for i in self.store.issues()}
        day = lambda n: (START + timedelta(days=n - 1)).isoformat()
        self.assertEqual(issues[("TT-003", "connection")]["closed_on"], day(3))
        self.assertEqual(issues[("IG-003", "permissions")]["closed_on"], day(4))
        self.assertEqual(issues[("XX-003", "connection")]["status"], "open")
        self.assertEqual(issues[("XX-003", "restriction")]["status"], "resolved")

    def test_check_failure_does_not_overwrite_inventory(self):
        self.run_days()
        self.assertEqual(self.store.get_account("LI-002").connection_status, "connected")

    def test_no_spurious_changes_on_day_one(self):
        self.run_days(1)
        events = {e["event_type"] for e in self.store.maintenance_log()}
        self.assertFalse(events & {"permission_change", "assets_change", "connection_change"})

    def test_reports_written(self):
        self.run_days()
        paths = reporting.write_all(self.store, Path(self.tmp.name))
        names = {p.name for p in paths}
        self.assertTrue({"dashboard.html", "maintenance_log.md", "issues.md", "inventory_export.csv"} <= names)
        payload = json.loads((Path(self.tmp.name) / "daily" / f"{START + timedelta(days=1)}.json").read_text())
        self.assertEqual((payload["accounts_checked"], payload["monitoring_day"]), (15, "Day 2"))
        log = (Path(self.tmp.name) / "maintenance_log.md").read_text()
        for expected in ("Day 5", "token_refresh", "assets_change", "issue_opened", "corrective_action"):
            self.assertIn(expected, log)

    def test_dashboard_shows_pending_days(self):
        self.run_days(2)
        html = reporting.write_dashboard(self.store, Path(self.tmp.name) / "d.html").read_text()
        self.assertIn("2/5 days", html)
        self.assertEqual(html.count(">pending<"), 3 * (5 + 15))  # 3 pending days x (platform + account rows)

    def test_handles_starting_with_at_sign_render_intact(self):
        # Regression: a leading '@' was once used as a layout marker and ate Instagram/TikTok/X handles.
        self.run_days(1)
        html = reporting.write_dashboard(self.store, Path(self.tmp.name) / "d.html").read_text()
        self.assertIn("<td>@spikerz.qa.ig01</td>", html)


class TestRobustnessStates(TempEnv):
    """States modelled on how a social-protection product actually fails, beyond simple token expiry."""

    def test_setup_incomplete_warns_then_clears(self):
        d1, d2 = self.run_days(2)
        self.assertIn("setup", [f["category"] for f in d1["IG-002"]["findings"]])
        self.assertEqual(d2["IG-002"]["severity"], "ok")

    def test_stale_token_metadata_opens_and_resolves_issue(self):
        d1, d2, d3 = self.run_days(3)
        self.assertEqual(d2["FB-001"]["connection_status"], "connected")
        self.assertIsNone(d2["FB-001"]["token_valid"])
        self.assertIn("token_metadata", [f["category"] for f in d2["FB-001"]["findings"]])
        issue = next(i for i in self.store.issues() if i["category"] == "token_metadata")
        self.assertEqual((issue["account_id"], issue["closed_on"]), ("FB-001", (START + timedelta(days=2)).isoformat()))

    def test_deactivated_account_is_error_and_recovers(self):
        d1, d2, d3, d4 = self.run_days(4)
        self.assertEqual((d3["FB-002"]["account_status"], d3["FB-002"]["severity"]), ("deactivated", "error"))
        issue = next(i for i in self.store.issues() if i["account_id"] == "FB-002")
        self.assertIn("deactivated", issue["root_cause"])
        self.assertEqual(issue["status"], "resolved")


class TestRouter(unittest.TestCase):
    def test_routes_by_account_provider_and_skips_unconfigured(self):
        mock_provider = MockProvider(START, SCENARIO)
        router = AccountRouter({"mock": mock_provider})
        live_acct = make_account(provider="live", credential_ref="env:X")
        self.assertTrue(router.handles(make_account()))
        self.assertFalse(router.handles(live_acct))
        with self.assertRaises(PermanentProviderError):
            router.check(live_acct, START)


class TestLiveProvider(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"SPIKERZ_QA_X_TEST": "dummy-token"})
        self.env.start()
        self.acct = Account("XX-901", "x", "business", "sandbox", "qa", credential_ref="env:SPIKERZ_QA_X_TEST")

    def tearDown(self):
        self.env.stop()

    def check(self, status, body):
        return LiveProvider(get=lambda url, h: (status, body)).check(self.acct, START)

    def test_x_ok(self):
        r = self.check(200, {"data": {"id": "42"}})
        self.assertEqual((r.connection_status, r.connected_assets), ("connected", ["x_user:42"]))

    def test_401_maps_to_expired(self):
        self.assertEqual(self.check(401, {"detail": "Unauthorized"}).connection_status, "expired")

    def test_403_maps_to_restricted(self):
        self.assertEqual(self.check(403, {}).account_status, "restricted")

    def test_http_client_maps_429_and_5xx_to_transient(self):
        import urllib.error
        for code in (429, 503):
            err = urllib.error.HTTPError("https://x", code, "x", {}, io.BytesIO(b"{}"))
            with mock.patch("urllib.request.urlopen", side_effect=err), self.assertRaises(TransientProviderError):
                http.get_json("https://x", {})

    def test_tiktok_body_error(self):
        acct = Account("TT-901", "tiktok", "business", "sandbox", "qa", credential_ref="env:SPIKERZ_QA_X_TEST")
        r = LiveProvider(get=lambda u, h: (200, {"error": {"code": "access_token_invalid", "message": "bad"}})
                         ).check(acct, START)
        self.assertEqual(r.connection_status, "error")

    def test_mock_ref_rejected(self):
        with self.assertRaises(PermanentProviderError):
            LiveProvider().check(make_account(), START)

    def test_bearer_header_sent(self):
        seen = {}

        def fake_get(url, headers):
            seen.update(headers)
            return 200, {"data": {"id": "1"}}
        LiveProvider(get=fake_get).check(self.acct, START)
        self.assertEqual(seen["Authorization"], "Bearer dummy-token")


class TestCli(TempEnv):
    def run_cli(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["--db", str(Path(self.tmp.name) / "cli.db"), "--actor", "t", *argv])

    def test_end_to_end(self):
        self.assertEqual(self.run_cli("import", str(INVENTORY)), 0)
        self.assertEqual(self.run_cli("list", "--platform", "x"), 0)
        self.assertEqual(self.run_cli("update", "LI-001", "--set", "notes=x"), 0)
        self.assertEqual(self.run_cli("update", "LI-001", "--set", "credential_ref=plaintext"), 1)
        self.assertEqual(self.run_cli("check"), 0)
        self.assertTrue((Path(self.tmp.name) / "dashboard.html").exists())
        self.assertEqual(self.run_cli("retire", "LI-001", "--reason", "test"), 0)


if __name__ == "__main__":
    unittest.main()
