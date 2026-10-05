"""Structured logging (JSON lines, rotating file) + request ids + in-process metrics."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

from app import config

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from app.db.store import current_merchant

        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "merchant_id": current_merchant.get(),
        }
        out.update({k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS and not k.startswith("_")})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(config.LOG_LEVEL)
    fh = RotatingFileHandler(config.LOG_DIR / "munim.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    ch.setLevel(logging.WARNING)
    root.addHandler(fh)
    root.addHandler(ch)
    for noisy in ("httpx", "httpcore", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


class Metrics:
    """Thread-safe counters + latency summaries. Local single-process, so in-memory is enough."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started = time.time()
        self.counters: dict[str, int] = defaultdict(int)
        self.latency: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])  # count, total, max

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counters[name] += n

    def observe(self, name: str, seconds: float) -> None:
        with self._lock:
            c = self.latency[name]
            c[0] += 1
            c[1] += seconds
            c[2] = max(c[2], seconds)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "uptime_s": round(time.time() - self.started),
                "counters": dict(sorted(self.counters.items())),
                "latency_ms": {k: {"count": v[0], "avg": round(v[1] / v[0] * 1000) if v[0] else 0,
                                   "max": round(v[2] * 1000)} for k, v in sorted(self.latency.items())},
            }


metrics = Metrics()
