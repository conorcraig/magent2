#!/usr/bin/env bash
set -euo pipefail

# Regenerate complexity baseline using xenon errors emitted under thresholds.

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${REPO_ROOT}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
fi
cd "${REPO_ROOT}"

BASELINE_PATH="${BASELINE_PATH:-.baseline-xenon}"
THRESHOLD_AVG="${THRESHOLD_AVG:-A}"
THRESHOLD_MODS="${THRESHOLD_MODS:-A}"
THRESHOLD_ABS="${THRESHOLD_ABS:-B}"
XENON_PATHS_ENV="${XENON_PATHS-}"
if [[ -n "${XENON_PATHS_ENV}" ]]; then
  # shellsplit into array from env var
  # shellcheck disable=SC2206
  XENON_PATHS_ARR=( ${XENON_PATHS_ENV} )
else
  XENON_PATHS_ARR=( magent2 scripts )
fi

echo "[update_complexity_baseline] Regenerating (ratchet-down-only) ${BASELINE_PATH}..."
echo "[update_complexity_baseline] Paths: ${XENON_PATHS_ARR[*]}"
echo "[update_complexity_baseline] Thresholds: AVG=${THRESHOLD_AVG} MODS=${THRESHOLD_MODS} ABS=${THRESHOLD_ABS}"
set +e
OUTPUT="$(uv run --isolated xenon \
  --max-average "${THRESHOLD_AVG}" \
  --max-modules "${THRESHOLD_MODS}" \
  --max-absolute "${THRESHOLD_ABS}" \
  --paths-in-front \
  "${XENON_PATHS_ARR[@]}" 2>&1)"
set -e

{ printf "%s\n" "${OUTPUT}" | grep -E '^ERROR:xenon:' || true; } > "${BASELINE_PATH}.new"

# If baseline does not exist or is empty, initialize it (first-time setup)
if [[ ! -s "${BASELINE_PATH}" ]]; then
  mv "${BASELINE_PATH}.new" "${BASELINE_PATH}"
  echo "[update_complexity_baseline] Initialized baseline at ${BASELINE_PATH}."
  exit 0
fi

# Abort if there are new errors not in the baseline (no ratchet up)
NEW_ADDITIONS=$(comm -13 <(sort -u "${BASELINE_PATH}") <(sort -u "${BASELINE_PATH}.new") || true)
if [[ -n "${NEW_ADDITIONS}" ]]; then
  echo "[update_complexity_baseline] New complexity errors detected relative to baseline. Aborting baseline update."
  rm -f "${BASELINE_PATH}.new"
  exit 1
fi

# No new errors; move forward to ratchet down (drop resolved lines)
mv "${BASELINE_PATH}.new" "${BASELINE_PATH}"
echo "[update_complexity_baseline] Baseline ratcheted down (if improvements existed). Review and commit changes."
