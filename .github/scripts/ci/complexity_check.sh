#!/usr/bin/env bash
set -euo pipefail

# Run xenon to enforce complexity thresholds and a baseline ratchet.
# If no paths are passed, run repo-wide and compare against baseline report.

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${REPO_ROOT}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
fi
cd "${REPO_ROOT}"

BASELINE_PATH=".baseline-xenon"
mkdir -p reports

THRESHOLD_AVG="A"
THRESHOLD_MODS="A"
THRESHOLD_ABS="B"

run_xenon() {
  uv run --isolated xenon \
    --max-average "${THRESHOLD_AVG}" \
    --max-modules "${THRESHOLD_MODS}" \
    --max-absolute "${THRESHOLD_ABS}" \
    --paths-in-front \
    "$@"
}

if [[ $# -gt 0 ]]; then
  echo "[complexity_check] Checking complexity for paths: $*"
  # In pre-commit, we only care about gating against thresholds, not baseline diff
  run_xenon "$@"
  exit $?
fi

echo "[complexity_check] Running repo-wide complexity check with baseline ratchet..."
set +e
XENON_OUTPUT="$(run_xenon magent2 scripts 2>&1)"
XENON_STATUS=$?
set -e

printf "%s\n" "${XENON_OUTPUT}" > reports/xenon-full.txt

# Normalize errors for stable diffs: keep xenon error lines, strip ":<line> ", sort
{ printf "%s\n" "${XENON_OUTPUT}" | grep -E '^ERROR:xenon:' || true; } \
  | sed -E 's/:([0-9]+) /:/' \
  | sort \
  > reports/xenon-errors.txt

[[ -f "${BASELINE_PATH}" ]] || touch "${BASELINE_PATH}"

# If there are any new errors beyond the baseline, fail. If baseline lines disappeared (improvement), also fail to force baseline update.
NEW_DIFF=$(diff -u <(sed -E 's/:([0-9]+) /:/' "${BASELINE_PATH}" | sort) reports/xenon-errors.txt || true)
if [[ -n "${NEW_DIFF}" ]]; then
  echo "[complexity_check] Complexity baseline drift detected. Review diff and update baseline if expected:"
  echo "${NEW_DIFF}"
  echo "[complexity_check] To update baseline after intentional improvements/fixes:"
  echo "[complexity_check]   bash .github/scripts/ci/update_complexity_baseline.sh"
  # Enforce failure on drift regardless of xenon threshold status
  exit 1
fi

# If thresholds failed but no drift occurred (possible if baseline is empty and thresholds exceeded), fail.
if [[ ${XENON_STATUS} -ne 0 ]]; then
  echo "[complexity_check] Xenon thresholds failed."
  exit ${XENON_STATUS}
fi

echo "[complexity_check] Complexity check passed thresholds and baseline ratchet."
exit 0
