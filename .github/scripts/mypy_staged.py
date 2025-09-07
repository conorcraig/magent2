#!/usr/bin/env python3
import os
import subprocess
import sys


def get_python_files_from_argv_or_git() -> list[str]:
    # If filenames were passed by pre-commit, use them; otherwise, detect staged Python files
    if len(sys.argv) > 1:
        return [p for p in sys.argv[1:] if p.endswith(".py")]

    result = subprocess.run(
        ["git", "diff", "--name-only", "--cached"],
        check=True,
        capture_output=True,
        text=True,
    )
    files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return [p for p in files if p.endswith(".py")]


def main() -> int:
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
    ).stdout.strip()
    os.chdir(repo_root)

    files = get_python_files_from_argv_or_git()
    if not files:
        print("mypy (staged): no Python files to check.")
        return 0

    # Run mypy only on the changed files for speed/PR ergonomics
    cmd = ["uv", "run", "mypy", "--show-error-codes", *files]
    proc = subprocess.run(cmd)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
