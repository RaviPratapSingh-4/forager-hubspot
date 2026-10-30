from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in os.getenv(name, default).split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    forager_api_key: str
    hubspot_token: str
    forager_account_id: int
    webhook_secret: str
    hubspot_client_secret: str
    public_base_url: str
    cache_dir: str
    default_include: tuple[str, ...]
    default_exclude: tuple[str, ...]
    default_seniority: tuple[str, ...]
    default_limit: int
    max_candidates: int
    personal_email_fallback: bool
    costs: dict
    forager_base: str = "https://api-v2.forager.ai"
    hubspot_base: str = "https://api.hubapi.com"

    def need(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError(f"Missing env vars for: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    return Settings(
        forager_api_key=os.getenv("FORAGER_API_KEY", ""),
        hubspot_token=os.getenv("HUBSPOT_TOKEN", ""),
        forager_account_id=int(os.getenv("FORAGER_ACCOUNT_ID", "2800")),
        webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
        hubspot_client_secret=os.getenv("HUBSPOT_CLIENT_SECRET", ""),
        public_base_url=os.getenv("PUBLIC_BASE_URL", ""),
        cache_dir=os.getenv("CACHE_DIR", ".cache"),
        default_include=_csv("DEFAULT_INCLUDE", ""),
        default_exclude=_csv("DEFAULT_EXCLUDE", "intern,assistant,student,recruiter"),
        default_seniority=_csv("DEFAULT_SENIORITY", "c_level,vp,director,head"),
        default_limit=int(os.getenv("DEFAULT_LIMIT", "5")),
        max_candidates=int(os.getenv("MAX_CANDIDATES", "15")),
        personal_email_fallback=os.getenv("PERSONAL_EMAIL_FALLBACK", "false").lower() == "true",
        costs=json.loads(os.getenv("FORAGER_COSTS", "{}") or "{}"),
    )


_STD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        d = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)),
            "level": r.levelname,
            "msg": r.getMessage(),
        }
        d.update({k: v for k, v in r.__dict__.items() if k not in _STD})
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, default=str)


def setup_logging() -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
