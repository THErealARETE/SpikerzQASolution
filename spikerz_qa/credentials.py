"""Credential *references*.

The database only ever stores a reference such as ``env:SPIKERZ_QA_LI_001_TOKEN``.
Secrets are resolved at runtime and never written to the DB, logs or reports.

Supported schemes:
  env:NAME        -> os.environ[NAME]
  vault:path      -> HashiCorp Vault KV v2 (requires VAULT_ADDR + VAULT_TOKEN; stubbed)
  aws-sm:name     -> AWS Secrets Manager (stubbed; wire boto3 in a real deployment)
  mock:anything   -> no secret; used by the mock provider
"""
from __future__ import annotations

import os
import re


class CredentialError(RuntimeError):
    pass


def resolve(ref: str) -> str | None:
    scheme, _, target = ref.partition(":")
    if scheme == "mock":
        return None
    if scheme == "env":
        value = os.environ.get(target)
        if not value:
            raise CredentialError(f"environment variable {target} is not set")
        return value
    if scheme in ("vault", "aws-sm"):
        raise CredentialError(
            f"'{scheme}' resolver is not configured in this build; "
            "see README > Security approach"
        )
    raise CredentialError(f"unknown credential scheme '{scheme}'")


_SECRET_LIKE = re.compile(r"(?i)(token|secret|password|bearer|access_token)=?[\"':\s]*[A-Za-z0-9\-_.~+/]{12,}")


def redact(text: str) -> str:
    """Mask anything that looks like a secret before it reaches a log or report."""
    return _SECRET_LIKE.sub(lambda m: m.group(1) + "=***REDACTED***", text)
