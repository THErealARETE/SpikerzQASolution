"""Health-check providers (Strategy pattern).

A provider answers one question: "what does the source say about this account
right now?" It never decides severity; that is ``healthcheck.evaluator``'s job.

Each account names its own provider (``Account.provider``); ``AccountRouter``
dispatches to the right one, so mock and real-source accounts share one run.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..models import Account
from .errors import PermanentProviderError, TransientProviderError

__all__ = ["Provider", "ProviderResult", "AccountRouter", "build_router",
           "TransientProviderError", "PermanentProviderError"]


@dataclass
class ProviderResult:
    account_status: str              # active | restricted | suspended | locked | deactivated
    connection_status: str           # connected | disconnected | expired | error | pending
    token_valid: bool | None         # None = not applicable / unknown / untrusted
    token_expires_at: str | None     # ISO-8601 UTC or None
    connected_assets: list[str] = field(default_factory=list)
    permissions: list[str] | None = None   # None = source does not expose granted scopes
    errors: list[str] = field(default_factory=list)
    source: str = "mock"
    setup_complete: bool | None = None   # Spikerz onboarding finished? None = unknown


class Provider(ABC):
    name: str = "base"

    @abstractmethod
    def check(self, account: Account, as_of: date) -> ProviderResult:
        """Return the state of one account as seen by this source."""

    def scripted_actions(self, as_of: date) -> list[dict[str, Any]]:
        """Operator actions a simulator wants recorded for this day (real sources: none)."""
        return []

    def handles(self, account: Account) -> bool:
        """Whether this run should check the account at all."""
        return True


class AccountRouter(Provider):
    """Dispatches each account to the provider named on the account record."""
    name = "router"

    def __init__(self, providers: dict[str, Provider]):
        self.providers = providers

    def check(self, account: Account, as_of: date) -> ProviderResult:
        provider = self.providers.get(account.provider)
        if provider is None:
            raise PermanentProviderError(f"provider '{account.provider}' is not configured for this run")
        return provider.check(account, as_of)

    def scripted_actions(self, as_of: date) -> list[dict[str, Any]]:
        return [a for p in self.providers.values() for a in p.scripted_actions(as_of)]

    def handles(self, account: Account) -> bool:
        return account.provider in self.providers


def build_router(monitoring_start: date, only: str | None = None) -> AccountRouter:
    """All providers by default; ``only`` restricts the run to one source."""
    from .live import LiveProvider
    from .mock import MockProvider
    factories = {"mock": lambda: MockProvider(monitoring_start), "live": LiveProvider}
    names = [only] if only else list(factories)
    return AccountRouter({n: factories[n]() for n in names})
