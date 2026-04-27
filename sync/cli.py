"""Command-line entry point for a manual sync.

Usage:
    python -m sync.cli --api-key YOUR_KEY
    GOOGLE_API_KEY=... python -m sync.cli
"""
from __future__ import annotations

import argparse
import os
import sys

# Force UTF-8 on Windows consoles so tab names with ⭐ don't crash prints.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

from sync.runner import run_sync


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sync the CotC sheet into local SQLite.")
    p.add_argument("--api-key", default=os.environ.get("GOOGLE_API_KEY"),
                   help="Google API key with Sheets API enabled. "
                        "Falls back to $GOOGLE_API_KEY.")
    args = p.parse_args(argv)
    if not args.api_key:
        print("ERROR: API key required (--api-key or $GOOGLE_API_KEY)", file=sys.stderr)
        return 2

    def progress(msg: str) -> None:
        print(msg, flush=True)

    try:
        summary = run_sync(args.api_key, progress=progress)
    except Exception as exc:
        print(f"SYNC FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"\nDone: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
