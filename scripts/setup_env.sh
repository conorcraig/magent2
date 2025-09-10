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
        # Prefer native uv checks to decide whether to sync
        if [ -d ".venv" ] && [ -x ".venv/bin/python" ]; then
            if uv lock --check >/dev/null 2>&1; then
                echo "uv: lock is current and .venv exists, skipping sync."
            else
                echo "uv: lock out of date; syncing dependencies (dev group)."
                uv sync --group dev
            fi
        else
            echo "uv: no usable .venv detected; creating/syncing (dev group)."
            uv sync --group dev
        fi
    fi
fi

# Install gh in user space (manual binary install) only if missing or wrong version
VER=2.61.0
NEED_GH_INSTALL=1
if ~/.local/bin/gh --version >/dev/null 2>&1; then
    installed_ver=$(~/.local/bin/gh --version | head -n1 | awk '{print $3}')
    if [ "$installed_ver" = "$VER" ]; then
        NEED_GH_INSTALL=0
        echo "gh ${VER} already installed, skipping download."
    fi
fi

if [ "$NEED_GH_INSTALL" -eq 1 ]; then
    ARCH=$(case "$(uname -m)" in x86_64) echo amd64 ;; aarch64|arm64) echo arm64 ;; *) echo amd64 ;; esac)
    curl -fsSL "https://github.com/cli/cli/releases/download/v${VER}/gh_${VER}_linux_${ARCH}.tar.gz" \
      | tar -xz --strip-components=2 -C "$HOME/.local/bin" "gh_${VER}_linux_${ARCH}/bin/gh"
fi

~/.local/bin/gh --version || true

# Non-interactive auth using GH_TOKEN if present (Cursor injects this)
# Non-interactive auth only if not already logged in
if ~/.local/bin/gh auth status >/dev/null 2>&1; then
    echo "gh auth: already logged in, skipping."
else
    if [ -n "${GH_TOKEN:-}" ]; then
        echo "$GH_TOKEN" | ~/.local/bin/gh auth login --with-token >/dev/null || true
    fi
    ~/.local/bin/gh auth status || true
fi

echo "Environment setup complete."
