#!/usr/bin/env bash
set -euo pipefail

# Regenerate complexity baseline using xenon errors emitted under thresholds.

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${REPO_ROOT}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
fi
cd "${REPO_ROOT}"

BASELINE_PATH=".baseline-xenon"
THRESHOLD_AVG="A"
THRESHOLD_MODS="A"
THRESHOLD_ABS="B"

echo "[update_complexity_baseline] Regenerating (ratchet-down-only) ${BASELINE_PATH}..."
set +e
OUTPUT="$(uv run --isolated xenon \
  --max-average "${THRESHOLD_AVG}" \
  --max-modules "${THRESHOLD_MODS}" \
  --max-absolute "${THRESHOLD_ABS}" \
  --paths-in-front \
  magent2 scripts 2>&1)"
set -e

{ printf "%s\n" "${OUTPUT}" | grep -E '^ERROR:xenon:' || true; } > "${BASELINE_PATH}.new"

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
