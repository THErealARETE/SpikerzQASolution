"""Single source of runtime configuration (paths + environment variables)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (stdlib only). Real environment variables always win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.split(" #")[0].strip())


def db_path() -> str:
    return os.environ.get("SPIKERZ_QA_DB", str(ROOT / "data" / "spikerz_qa.db"))


def reports_dir() -> Path:
    return Path(os.environ.get("SPIKERZ_QA_REPORTS", ROOT / "reports"))


def alerts_file() -> Path:
    return Path(os.environ.get("SPIKERZ_QA_ALERTS_FILE", reports_dir() / "alerts.jsonl"))


def alert_webhook() -> str | None:
    return os.environ.get("SPIKERZ_QA_ALERT_WEBHOOK") or None


def default_provider() -> str:
    return os.environ.get("SPIKERZ_QA_PROVIDER", "auto")


def scenario_path() -> Path:
    return Path(os.environ.get("SPIKERZ_QA_SCENARIO", ROOT / "data" / "scenario.json"))

