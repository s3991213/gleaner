"""Structured JSON logging that is safe for a public proxy.

Logs never contain raw client IPs or full feed URLs. Private feed URLs often carry
access tokens in the path or query, so only the host is logged. Client IPs are
replaced by a keyed hash, so repeat visitors can be correlated without storing
who they are. The key comes from LOG_HASH_KEY (a Key Vault secret in Azure).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
from datetime import UTC, datetime

APP_VERSION = os.environ.get("APP_VERSION", "dev")

_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "version": APP_VERSION,
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("gleaner")
    root.handlers[:] = [handler]
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))
    root.propagate = False


class ClientHasher:
    """HMAC client addresses so logs can group requests without storing IPs."""

    def __init__(self, key: str | None = None) -> None:
        raw = key if key is not None else os.environ.get("LOG_HASH_KEY")
        # Without a configured key, use a random one: hashes stay private but
        # cannot be correlated across restarts.
        self.key = raw.encode() if raw else secrets.token_bytes(32)
        self.configured = bool(raw)

    def __call__(self, ip: str) -> str:
        return hmac.new(self.key, ip.encode(), hashlib.sha256).hexdigest()[:16]
