#!/usr/bin/env bash
set -euo pipefail

# Environment setup for agents (Linux, user-space only)
# - Installs uv if missing
# - Installs GitHub CLI to ~/.local/bin via manual binary download
# - Installs 'just' task runner to ~/.local/bin via GitHub release assets
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

# Ensure 'just' task runner is available (user-space install)
if ! command -v just >/dev/null 2>&1; then
    echo "Installing just (user-space)..."
    JUST_TMP="$(mktemp -d)"
    # Resolve arch triple
    JARCH="x86_64"; case "$(uname -m)" in x86_64|amd64) JARCH="x86_64" ;; aarch64|arm64) JARCH="aarch64" ;; *) JARCH="x86_64" ;; esac
    # Fetch latest tag
    TAG=$(curl -fsSL https://api.github.com/repos/casey/just/releases/latest | grep -m1 '"tag_name"' | sed -E 's/.*"([^"[:space:]]+)".*/\1/') || TAG="v1.36.0"
    # Prefer musl static builds for portability; fallback to gnu if needed
    ASSET_MUSL="just-${TAG}-${JARCH}-unknown-linux-musl.tar.gz"
    ASSET_GNU="just-${TAG}-${JARCH}-unknown-linux-gnu.tar.gz"
    URL_BASE="https://github.com/casey/just/releases/download/${TAG}"
    URL="${URL_BASE}/${ASSET_MUSL}"
    if ! curl -fsI "$URL" >/dev/null 2>&1; then
        URL="${URL_BASE}/${ASSET_GNU}"
    fi
    # Download and extract
    if curl -fsSL "$URL" | tar -xz -C "$JUST_TMP" 2>/dev/null; then
        # Find the 'just' binary and install it
        if [ -f "$JUST_TMP/just" ]; then
            install -m 0755 "$JUST_TMP/just" "$HOME/.local/bin/just"
        else
            # Try to locate within extracted directory
            FOUND=$(find "$JUST_TMP" -type f -name just -maxdepth 2 | head -n1 || true)
            if [ -n "${FOUND:-}" ]; then
                install -m 0755 "$FOUND" "$HOME/.local/bin/just"
            else
                echo "warning: could not locate 'just' binary in archive" >&2
            fi
        fi
    else
        echo "warning: failed to download or extract just from $URL" >&2
    fi
    rm -rf "$JUST_TMP"
fi

# Show just version if available
if command -v just >/dev/null 2>&1; then
    just --version || true
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
