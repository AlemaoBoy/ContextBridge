"""Verify overall and critical-module statement + branch coverage gates from coverage.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

OVERALL_MIN_PERCENT = 95.0
CRITICAL_GATES: dict[str, tuple[tuple[str, ...], float]] = {
    "security/": (("src/contextbridge/security/",), 95.0),
    "backup/": (("src/contextbridge/backup/",), 95.0),
    "skills/migrator.py": (("src/contextbridge/skills/migrator.py",), 95.0),
    "models/context.py": (("src/contextbridge/models/context.py",), 95.0),
    "context/files.py": (("src/contextbridge/context/files.py",), 95.0),
    "context/transfer.py": (("src/contextbridge/context/transfer.py",), 95.0),
    "context/handoff.py": (("src/contextbridge/context/handoff.py",), 95.0),
    "context/local_sessions.py": (("src/contextbridge/context/local_sessions.py",), 95.0),
    "context_cli.py": (("src/contextbridge/context_cli.py",), 95.0),
}


def _calc_combined_percent(summary: dict[str, int]) -> float:
    """Compute coverage.py's combined statement + branch coverage percentage."""
    stmts = summary.get("num_statements", 0)
    covered_stmts = summary.get("covered_lines", 0)
    branches = summary.get("num_branches", 0)
    covered_branches = summary.get("covered_branches", 0)
    total = stmts + branches
    if total == 0:
        return 100.0
    return ((covered_stmts + covered_branches) / total) * 100.0


def main() -> int:
    report_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("coverage.json")
    if not report_path.is_file():
        print(f"ERROR: coverage JSON report not found: {report_path}", file=sys.stderr)
        return 2

    data = json.loads(report_path.read_text(encoding="utf-8"))
    totals = data["totals"]
    overall_pct = _calc_combined_percent(totals)

    failures: list[str] = []
    print(
        f"Overall combined coverage (statements + branches): {overall_pct:.2f}% "
        f"(min {OVERALL_MIN_PERCENT:.2f}%)"
    )
    if overall_pct < OVERALL_MIN_PERCENT:
        failures.append(f"Overall coverage {overall_pct:.2f}% < {OVERALL_MIN_PERCENT:.2f}%")

    files: dict[str, dict[str, object]] = data["files"]
    for label, (prefixes, threshold) in CRITICAL_GATES.items():
        agg = {
            "num_statements": 0,
            "covered_lines": 0,
            "num_branches": 0,
            "covered_branches": 0,
        }
        matched = 0
        for raw_path, file_data in files.items():
            norm = raw_path.replace("\\", "/")
            if any(norm.startswith(prefix) or norm == prefix for prefix in prefixes):
                matched += 1
                summary = file_data["summary"]
                assert isinstance(summary, dict)
                for key in agg:
                    agg[key] += int(summary.get(key, 0))

        if matched == 0:
            failures.append(f"Critical gate {label!r} matched 0 files in {report_path}")
            continue

        pct = _calc_combined_percent(agg)
        status = "PASS" if pct >= threshold else "FAIL"
        print(
            f"Critical gate {label:20s}: {pct:6.2f}% "
            f"(min {threshold:.2f}%, {matched} file(s)) [{status}]"
        )
        if pct < threshold:
            failures.append(f"Critical gate {label} coverage {pct:.2f}% < {threshold:.2f}%")

    if failures:
        print("\nCoverage gate FAILED:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print("\nAll overall and critical-module coverage gates PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
