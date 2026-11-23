"""Live adapters for each platform's official, read-only API.

NOT exercised against real accounts in this submission (ASSUMPTIONS.md, A-02):
no credentials or test environment were supplied. They are unit-tested with
stubbed HTTP and show how ``mock`` is swapped for real checks without touching
the rest of the tool. No posting, no scraping, no login automation.
"""
from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable

from .. import http
from ..credentials import CredentialError, resolve
from ..models import Account
from . import PermanentProviderError, Provider, ProviderResult

GRAPH = "https://graph.facebook.com/v21.0"


@dataclass(frozen=True)
class Endpoint:
    """A 'who am I' style endpoint and how to turn its body into connected assets."""
    url: str
    assets: Callable[[dict[str, Any]], list[str]]
    body_error: Callable[[dict[str, Any]], str | None] = lambda body: None


ENDPOINTS: dict[str, Endpoint] = {
    "instagram": Endpoint(
        "https://graph.instagram.com/v21.0/me?fields=user_id,username,account_type",
        lambda b: [f"ig_account:{b.get('user_id', b.get('id'))}"]),
    "linkedin": Endpoint(
        "https://api.linkedin.com/rest/organizationAcls?q=roleAssignee&state=APPROVED",
        lambda b: [f"org:{e.get('organization', '').rsplit(':', 1)[-1]}" for e in b.get("elements", [])]),
    "tiktok": Endpoint(
        "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name",
        lambda b: [f"tiktok_user:{b.get('data', {}).get('user', {}).get('open_id')}"],
        lambda b: None if b.get("error", {}).get("code") in (None, "ok")
        else f"{b['error'].get('code')}: {b['error'].get('message')}"),
    "x": Endpoint(
        "https://api.x.com/2/users/me?user.fields=protected",
        lambda b: [f"x_user:{b.get('data', {}).get('id')}"]),
}


class LiveProvider(Provider):
    name = "live"

    def __init__(self, get: http.HttpGet = http.get_json):
        self._http_get = get

    def check(self, account: Account, as_of: date) -> ProviderResult:
        token = self._token(account)
        if account.platform == "facebook":
            return self._check_facebook(token)
        return self._check_endpoint(ENDPOINTS[account.platform], token)

    # ------------------------------------------------------------------ steps
    @staticmethod
    def _token(account: Account) -> str:
        try:
            token = resolve(account.credential_ref)
        except CredentialError as exc:
            raise PermanentProviderError(str(exc)) from exc
        if not token:
            raise PermanentProviderError("live provider requires a real credential_ref (not mock:)")
        return token

    def _get(self, url: str, token: str | None = None) -> tuple[int, dict[str, Any]]:
        return self._http_get(url, http.bearer(token) if token else {})

    def _result(self, status: str = "connected", assets: list[str] | None = None, **kw: Any) -> ProviderResult:
        kw.setdefault("token_valid", status == "connected")
        return ProviderResult(account_status=kw.pop("account_status", "active"), connection_status=status,
                              token_expires_at=kw.pop("token_expires_at", None),
                              connected_assets=assets or [], source=self.name, **kw)

    def _auth_failure(self, status: int, body: dict[str, Any]) -> ProviderResult | None:
        """Map auth failures to a result; other 4xx are permanent errors."""
        if status == 401:
            return self._result("expired", errors=[f"HTTP 401: {_msg(body)}"])
        if status == 403:
            return self._result("error", account_status="restricted", token_valid=None,
                                errors=[f"HTTP 403: {_msg(body)}"])
        if status >= 400:
            raise PermanentProviderError(f"HTTP {status}: {_msg(body)}")
        return None

    def _check_endpoint(self, ep: Endpoint, token: str) -> ProviderResult:
        status, body = self._get(ep.url, token)
        if (failure := self._auth_failure(status, body)):
            return failure
        if (err := ep.body_error(body)):
            return self._result("error", token_valid=None, errors=[err])
        return self._result("connected", ep.assets(body))

    def _check_facebook(self, token: str) -> ProviderResult:
        app_token = os.environ.get("META_APP_ACCESS_TOKEN")
        if not app_token:
            raise PermanentProviderError("META_APP_ACCESS_TOKEN not set (needed for debug_token)")
        q = urllib.parse.urlencode({"input_token": token, "access_token": app_token})
        status, body = self._get(f"{GRAPH}/debug_token?{q}")
        if (failure := self._auth_failure(status, body)):
            return failure
        data = body.get("data", {})
        _, pages = self._get(f"{GRAPH}/me/accounts?fields=id,name", token)
        return self._result(
            "connected" if data.get("is_valid") else "expired",
            [f"page:{p['id']}" for p in pages.get("data", [])],
            token_expires_at=_epoch(data.get("expires_at")),
            permissions=sorted(data.get("scopes", [])),
            errors=[data["error"]["message"]] if data.get("error") else [])


def _msg(body: dict[str, Any]) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("message", err))
    return str(err or body.get("detail") or body.get("message") or "no body")


def _epoch(value: Any) -> str | None:
    if not value:
        return None  # Meta returns 0 for never-expiring page tokens
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
