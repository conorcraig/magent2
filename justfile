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
# One-shot entrypoints
# ──────────────────────────────────────────────────────────────────────────────
check:
	# Full local quality gate: lint, types, complexity, secrets, tests
	# Whitespace/EOL auto-fixes across the repo
	uv run --isolated pre-commit run end-of-file-fixer --all-files || true
	uv run --isolated pre-commit run trailing-whitespace --all-files || true
	uv run --isolated ruff format .
	uv run --isolated ruff check --fix .
	# Markdown auto-fix (uses local Node via npx)
	npx --yes markdownlint-cli2 --fix || true
	bash .github/scripts/ci/type_check.sh
	bash .github/scripts/ci/complexity_check.sh
	# Validate secrets against baseline using pre-commit hook across tracked files (excluding baselines)
	bash -c 'FILES=$(git ls-files | grep -v "^\\.baseline-"); uv run --isolated python -m detect_secrets.pre_commit_hook --baseline .baseline-secrets -- $FILES'
	uv run --isolated pytest -q

update:
	# Update all baselines in one go
	bash .github/scripts/ci/update_type_baseline.sh
	bash .github/scripts/ci/update_complexity_baseline.sh
	uv run --isolated detect-secrets scan --exclude-files '^\\.baseline-.*$' > .baseline-secrets

test:
	uv run --isolated pytest -q

up +args:
	docker compose up -d {{args}}

down +args:
	docker compose down {{args}}
