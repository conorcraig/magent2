#!/usr/bin/env bash
set -euo pipefail

# Environment setup for agents (Linux, user-space only)
# - Installs uv if missing
# - Installs GitHub CLI to ~/.local/bin via manual binary download
# - Authenticates gh non-interactively via GH_TOKEN if available
# - Syncs Python dependencies (including dev group) via uv if pyproject.toml exists

mkdir -p "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$PATH"

# Install uv if not present
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# Verify uv and sync dependencies if project is Python-based
if command -v uv >/dev/null 2>&1; then
    uv --version || true
    if [ -f "pyproject.toml" ]; then
        # Install default + dev dependency groups
        uv sync --group dev
    fi
fi

# Install gh in user space (manual binary install)
VER=2.61.0
ARCH=$(case "$(uname -m)" in x86_64) echo amd64 ;; aarch64|arm64) echo arm64 ;; *) echo amd64 ;; esac)
curl -fsSL "https://github.com/cli/cli/releases/download/v${VER}/gh_${VER}_linux_${ARCH}.tar.gz" \
  | tar -xz --strip-components=2 -C "$HOME/.local/bin" "gh_${VER}_linux_${ARCH}/bin/gh"

~/.local/bin/gh --version || true

# Non-interactive auth using GH_TOKEN if present (Cursor injects this)
if [ -n "${GH_TOKEN:-}" ]; then
    echo "$GH_TOKEN" | ~/.local/bin/gh auth login --with-token >/dev/null
fi
~/.local/bin/gh auth status || true

echo "Environment setup complete."
