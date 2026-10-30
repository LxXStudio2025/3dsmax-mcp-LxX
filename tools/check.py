"""Run source quality checks and offline protocol regressions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = (
    ("lint", ["-m", "ruff", "check", "src", "tools", "tests"]),
    ("format", ["-m", "ruff", "format", "--check", "src", "tools", "tests"]),
    ("unit", ["-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]),
    ("smoke", ["tests/smoke.py"]),
    ("regression", ["tests/regress.py"]),
    ("compile", ["-m", "compileall", "-q", "src", "tools", "tests"]),
)


def main() -> int:
    failures = []
    for name, arguments in CHECKS:
        result = subprocess.run([sys.executable, *arguments], cwd=ROOT, check=False)
        print(f"{name}: {'PASS' if result.returncode == 0 else 'FAIL'}", flush=True)
        if result.returncode:
            failures.append(name)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
