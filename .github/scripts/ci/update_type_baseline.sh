#!/usr/bin/env bash
set -euo pipefail

# Regenerate mypy baseline from current repository state.

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${REPO_ROOT}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
fi
cd "${REPO_ROOT}"

BASELINE_PATH=".baseline-mypy"

echo "[update_type_baseline] Generating baseline at ${BASELINE_PATH}..."
uv run mypy . | uv run mypy-baseline sync --baseline-path "${BASELINE_PATH}" --sort-baseline
echo "[update_type_baseline] Baseline updated. Review and commit changes."
