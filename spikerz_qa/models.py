"""Domain model: enums, platform policy, severity, Account and Finding."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

PLATFORMS = ("linkedin", "facebook", "instagram", "tiktok", "x")
PLATFORM_LABEL = {"linkedin": "LinkedIn", "facebook": "Facebook", "instagram": "Instagram",
                  "tiktok": "TikTok", "x": "X"}
ACCOUNT_TYPES = ("personal_profile", "page", "business", "creator", "organization")
ENVIRONMENTS = ("mock", "sandbox", "staging", "production_test")
# Where an account's health is read from (see providers/). One account, one source.
PROVIDERS = ("mock", "live")
ACCOUNT_STATUSES = ("active", "restricted", "suspended", "locked", "deactivated", "retired")
CONNECTION_STATUSES = ("pending", "connected", "disconnected", "expired", "error")

SEVERITY_RANK = {"ok": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
SEVERITIES = tuple(SEVERITY_RANK)

MIN_PER_PLATFORM = 3
TOKEN_EXPIRY_WARNING_DAYS = 7

# Scopes Spikerz is ASSUMED to need per platform (ASSUMPTIONS.md, A-07).
REQUIRED_PERMISSIONS: dict[str, list[str]] = {
    "facebook": ["pages_show_list", "pages_read_engagement", "pages_read_user_content",
                 "pages_manage_engagement", "pages_manage_metadata"],
    "instagram": ["instagram_business_basic", "instagram_business_manage_comments"],
    "linkedin": ["r_organization_social", "w_organization_social", "rw_organization_admin"],
    "tiktok": ["user.info.basic", "video.list"],
    "x": ["tweet.read", "users.read", "tweet.moderate.write", "offline.access"],
}

CREDENTIAL_REF_PATTERN = re.compile(r"^(env|vault|aws-sm|mock):[A-Za-z0-9_./\-]+$")
ACCOUNT_ID_PATTERN = re.compile(r"^[A-Z]{2}-\d{3}$")

LIST_FIELDS = ("connected_assets", "permissions")
MUTABLE_FIELDS = ("account_type", "environment", "owner_team", "status", "connection_status",
                  "connected_assets", "permissions", "notes", "credential_ref", "handle",
                  "provider")


class ValidationError(ValueError):
    """Raised when an account record fails validation."""


def worst(severities: Iterable[str], default: str = "ok") -> str:
    return max(severities, key=SEVERITY_RANK.__getitem__, default=default)


def at_least(severity: str, threshold: str) -> bool:
    return SEVERITY_RANK[severity] >= SEVERITY_RANK[threshold]


def split_list(value: Any) -> list[str]:
    """CSV/CLI lists are pipe-separated: 'page:1|page:2'."""
    if isinstance(value, list):
        return value
    return [v.strip() for v in str(value or "").split("|") if v.strip()]


def join_list(values: Iterable[str]) -> str:
    return "|".join(values)


@dataclass(frozen=True)
class Finding:
    severity: str
    category: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class Account:
    account_id: str
    platform: str
    account_type: str
    environment: str
    owner_team: str
    handle: str = ""
    status: str = "active"
    connection_status: str = "pending"
    connected_assets: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    last_health_check: str | None = None
    notes: str = ""
    credential_ref: str = ""
    provider: str = "mock"          # which Provider reports this account's health
    created_at: str | None = None
    updated_at: str | None = None

    _ENUMS = {"platform": PLATFORMS, "account_type": ACCOUNT_TYPES, "environment": ENVIRONMENTS,
              "status": ACCOUNT_STATUSES, "connection_status": CONNECTION_STATUSES, "provider": PROVIDERS}

    def validate(self) -> "Account":
        errors = [f"{name} '{getattr(self, name)}' not in {allowed}"
                  for name, allowed in self._ENUMS.items() if getattr(self, name) not in allowed]
        if not ACCOUNT_ID_PATTERN.match(self.account_id or ""):
            errors.append(f"account_id '{self.account_id}' must look like 'LI-001'")
        if not self.owner_team:
            errors.append("owner_team is required")
        if not CREDENTIAL_REF_PATTERN.match(self.credential_ref or ""):
            errors.append("credential_ref must be a reference such as 'env:VAR_NAME' or "
                          "'vault:path/to/secret' - never a raw secret")
        if not all(isinstance(getattr(self, f), list) for f in LIST_FIELDS):
            errors.append("connected_assets and permissions must be lists")
        if errors:
            raise ValidationError(f"{self.account_id}: " + "; ".join(errors))
        return self

    @property
    def required_permissions(self) -> list[str]:
        return REQUIRED_PERMISSIONS.get(self.platform, [])

    @property
    def label(self) -> str:
        return PLATFORM_LABEL[self.platform]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Account":
        clean = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        for list_field in LIST_FIELDS:
            if list_field in clean:
                clean[list_field] = split_list(clean[list_field])
        return cls(**clean)
