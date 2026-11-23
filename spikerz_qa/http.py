"""The one HTTP client used by every provider (stdlib only, injectable for tests).

Returns (status, json_body). Network failures, 429 and 5xx raise
TransientProviderError so the retry layer can handle them uniformly.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Callable

from .providers.errors import TransientProviderError

TIMEOUT_SECONDS = 20
USER_AGENT = "spikerz-qa-healthcheck/1.0"

HttpGet = Callable[[str, dict[str, str]], tuple[int, Any]]


def basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_json(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TransientProviderError(f"network error: {exc}") from exc
    if status == 429 or status >= 500:
        raise TransientProviderError(f"HTTP {status}")
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}
