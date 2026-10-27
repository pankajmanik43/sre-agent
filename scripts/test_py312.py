"""Run the test suite on a locally installed Python 3.12 via uv, or skip if there isn't one.

Never downloads an interpreter: uv is called with --no-python-downloads throughout.
Usage: python scripts/test_py312.py [extra pytest args]
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "3.12"


def main(pytest_args: list[str]) -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("SKIP: uv is not installed.")
        return 0

    found = subprocess.run(  # noqa: S603
        [uv, "python", "find", VERSION, "--no-python-downloads"],
        capture_output=True,
        text=True,
        check=False,
    )
    if found.returncode != 0:
        print(f"SKIP: Python {VERSION} is not installed locally (nothing was downloaded).")
        return 0

    interpreter = found.stdout.strip()
    print(f"Running tests on {interpreter}")
    # --isolated builds a throwaway environment, so the project's .venv is left alone.
    return subprocess.run(  # noqa: S603
        [
            uv,
            "run",
            "--isolated",
            "--no-python-downloads",
            "--python",
            interpreter,
            "--extra",
            "dev",
            "pytest",
            *pytest_args,
        ],
        cwd=ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
