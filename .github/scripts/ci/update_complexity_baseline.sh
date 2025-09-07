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

echo "[update_complexity_baseline] Regenerating ${BASELINE_PATH}..."
set +e
OUTPUT="$(uv run xenon \
  --max-average "${THRESHOLD_AVG}" \
  --max-modules "${THRESHOLD_MODS}" \
  --max-absolute "${THRESHOLD_ABS}" \
  --paths-in-front \
  magent2 scripts 2>&1)"
set -e

{ printf "%s\n" "${OUTPUT}" | grep -E '^ERROR:xenon:' || true; } > "${BASELINE_PATH}.tmp"
mv "${BASELINE_PATH}.tmp" "${BASELINE_PATH}"
echo "[update_complexity_baseline] Baseline updated. Review and commit changes."
