"""Start Munim AI locally:   python -m app.serve   (creates the database + demo merchant on first run)."""
from __future__ import annotations

import sys

import uvicorn

from app import config
from app.db.seed import bootstrap
from app.observability import setup_logging


def main() -> int:
    errors, warnings = config.validate()
    for w in warnings:
        print(f"WARNING: {w}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        print("Fix .env (see .env.example) and start again.")
        return 1
    setup_logging()
    bootstrap()  # prints the demo login once, the first time
    print(f"Munim AI running on http://{config.HOST}:{config.PORT}   ({config.mode_summary()})", flush=True)
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, log_level="warning", workers=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
