#!/usr/bin/env python3
"""Fail when any integration module is not strictly above a coverage floor.

Reads the JSON report produced by ``coverage json`` and exits non-zero when
any measured file has ``percent_covered <= --min``. The rule is "above 95%",
so a module at exactly the floor fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report",
        nargs="?",
        default="coverage.json",
        help="coverage JSON report written by `coverage json`",
    )
    parser.add_argument(
        "--min",
        type=float,
        default=95.0,
        dest="minimum",
        help="exclusive per-module percentage floor (default: 95)",
    )
    return parser.parse_args(argv)


def _short_name(path: str) -> str:
    return Path(path).name


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"coverage report not found: {args.report}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as err:
        print(f"malformed coverage report {args.report}: {err}", file=sys.stderr)
        return 2

    files = report.get("files", {})
    if not files:
        print("coverage report contains no files", file=sys.stderr)
        return 2

    offenders: list[tuple[str, float]] = []
    for path, data in sorted(files.items()):
        percent = float(data["summary"]["percent_covered"])
        marker = "FAIL" if percent <= args.minimum else "ok"
        print(f"{marker:4} {percent:6.2f}%  {_short_name(path)}")
        if percent <= args.minimum:
            offenders.append((_short_name(path), percent))

    if offenders:
        print(
            f"\n{len(offenders)} module(s) at or below {args.minimum:g}% "
            "(the rule requires strictly above):",
            file=sys.stderr,
        )
        for name, percent in offenders:
            print(f"  {name}: {percent:.2f}%", file=sys.stderr)
        return 1

    print(f"\nAll {len(files)} module(s) are above {args.minimum:g}%.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
