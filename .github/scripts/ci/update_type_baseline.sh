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

# Run mypy once
set +e
MYPY_OUTPUT="$(uv run --isolated mypy . 2>&1)"
MYPY_STATUS=$?
set -e

if [[ ${MYPY_STATUS} -ne 0 ]]; then
  echo "[update_type_baseline] mypy reported errors. Aborting baseline update (no ratchet up)."
  echo "[update_type_baseline] Run: bash .github/scripts/ci/type_check.sh to see details."
  exit 1
fi

# Detect regressions (new errors vs baseline). If any new errors, abort.
NEW_ERRORS=$(printf "%s\n" "${MYPY_OUTPUT}" | uv run --isolated mypy-baseline filter --baseline-path "${BASELINE_PATH}" --hide-stats --no-colors || true)
if [[ -n "${NEW_ERRORS}" ]]; then
  echo "[update_type_baseline] New type errors detected relative to baseline. Aborting baseline update."
  exit 1
fi

# No regressions; sync to drop resolved errors (ratchet down)
printf "%s\n" "${MYPY_OUTPUT}" | uv run --isolated mypy-baseline sync --baseline-path "${BASELINE_PATH}" --sort-baseline
echo "[update_type_baseline] Baseline ratcheted down (if improvements existed). Review and commit changes."
