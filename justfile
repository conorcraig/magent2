# Task runner: justfile
# Recipes wrap existing scripts for consistency.

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

alias default := help

# ──────────────────────────────────────────────────────────────────────────────
# Help
# ──────────────────────────────────────────────────────────────────────────────
help:
	@just --list

# ──────────────────────────────────────────────────────────────────────────────
# Quality gates
# ──────────────────────────────────────────────────────────────────────────────
lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	bash .github/scripts/ci/type_check.sh

complexitycheck +args:
	bash .github/scripts/ci/complexity_check.sh {{args}}

# ──────────────────────────────────────────────────────────────────────────────
# Baselines
# ──────────────────────────────────────────────────────────────────────────────
update-type-baseline:
	bash .github/scripts/ci/update_type_baseline.sh

update-complexity-baseline:
	bash .github/scripts/ci/update_complexity_baseline.sh

# ──────────────────────────────────────────────────────────────────────────────
# Secrets
# ──────────────────────────────────────────────────────────────────────────────
secrets-scan:
	uv run detect-secrets scan | diff -u .baseline-secrets -

secrets-baseline:
	uv run detect-secrets scan > .baseline-secrets

# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────
test:
	uv run pytest -q

# ──────────────────────────────────────────────────────────────────────────────
# Docker convenience
# ──────────────────────────────────────────────────────────────────────────────
up +args:
	docker compose up -d {{args}}

down +args:
	docker compose down {{args}}
