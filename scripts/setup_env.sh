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
        # If lock is current and venv exists, skip; otherwise sync dev deps
        if uv lock --check >/dev/null 2>&1 && [ -d ".venv" ] && [ -x ".venv/bin/python" ]; then
            echo "uv: lock current and .venv present; skipping sync."
        else
            echo "uv: syncing dependencies (dev group)."
            uv sync --group dev
        fi
    fi
fi

# Install gh in user space (manual binary install) only if missing or wrong version
VER=2.61.0
NEED_GH_INSTALL=1
if command -v gh >/dev/null 2>&1; then
    installed_ver=$(gh --version | head -n1 | awk '{print $3}')
    if [ "$installed_ver" = "$VER" ]; then
        NEED_GH_INSTALL=0
        echo "gh ${VER} already installed, skipping download."
    fi
fi

if [ "$NEED_GH_INSTALL" -eq 1 ]; then
    ARCH="amd64"
    case "$(uname -m)" in
        x86_64|amd64) ARCH="amd64" ;;
        aarch64|arm64) ARCH="arm64" ;;
        *) ARCH="amd64" ;;
    esac
    curl -fsSL "https://github.com/cli/cli/releases/download/v${VER}/gh_${VER}_linux_${ARCH}.tar.gz" \
      | tar -xz --strip-components=2 -C "$HOME/.local/bin" "gh_${VER}_linux_${ARCH}/bin/gh"
fi

# Confirm gh availability
if command -v gh >/dev/null 2>&1; then
    gh --version || true
else
    echo "warning: gh not found on PATH after install attempt" >&2
fi

# Non-interactive auth using GH_TOKEN if present (Cursor injects this)
# Non-interactive auth only if not already logged in
if command -v gh >/dev/null 2>&1; then
    if gh auth status >/dev/null 2>&1; then
        echo "gh auth: already logged in, skipping."
    else
        if [ -n "${GH_TOKEN:-}" ]; then
            echo "$GH_TOKEN" | gh auth login --with-token >/dev/null || true
        fi
        gh auth status || true
    fi
fi

echo "Environment setup complete."
