#!/usr/bin/env bash
set -euo pipefail

# Run mypy for the entire repo and gate using mypy-baseline's filter to enforce a ratchet.
# Fails if there are new type errors OR if previously-baselined errors were resolved
# (forcing a baseline update).

# Determine repo root and move there
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${REPO_ROOT}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
fi
cd "${REPO_ROOT}"

BASELINE_PATH=".baseline-mypy"
mkdir -p reports
[[ -f "${BASELINE_PATH}" ]] || touch "${BASELINE_PATH}"

echo "[type_check] Running mypy and enforcing baseline ratchet..."

# Capture full mypy output for logs
set +e
MYPY_OUTPUT="$(uv run mypy . 2>&1)"
MYPY_STATUS=$?
set -e

printf "%s\n" "${MYPY_OUTPUT}" > reports/mypy-full.txt

# Pipe through mypy-baseline filter to allow only regressions through (and fail on improvements)
printf "%s\n" "${MYPY_OUTPUT}" | uv run mypy-baseline filter --baseline-path "${BASELINE_PATH}" --sort-baseline
FILTER_STATUS=$?

if [[ ${FILTER_STATUS} -ne 0 ]]; then
  echo "[type_check] mypy-baseline detected baseline drift (regression or improvement)."
  echo "[type_check] To sync the baseline after fixing or intentionally accepting improvements, run:"
  echo "[type_check]   bash .github/scripts/ci/update_type_baseline.sh"
  exit ${FILTER_STATUS}
fi

echo "[type_check] mypy passed the ratchet gate."
exit 0
