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
	@printf "\033[1;36m==> Preparing reports directory\033[0m\n"
	@mkdir -p reports
	@printf "\033[1;36m==> Ensuring pre-commit hooks are installed\033[0m\n"
	@uv run --isolated pre-commit install --install-hooks || true
	# Whitespace/EOL auto-fixes across the repo
	@printf "\033[1;36m==> Whitespace/EOL auto-fixes (pre-commit)\033[0m\n"
	@uv run --isolated pre-commit run end-of-file-fixer --all-files |& tee reports/pre-commit-end-of-file-fixer.log | sed -E '/^Installed [0-9]+ packages in /d' || true
	@uv run --isolated pre-commit run trailing-whitespace --all-files |& tee reports/pre-commit-trailing-whitespace.log | sed -E '/^Installed [0-9]+ packages in /d' || true
	# Ruff format and lint via uv (single source of truth: pyproject.toml)
	@printf "\033[1;36m==> Ruff: format (apply)\033[0m\n"
	@uv run --isolated ruff format . |& tee reports/ruff-format.log | sed -E '/^Installed [0-9]+ packages in /d'
	@printf "\033[1;36m==> Ruff: check (apply --fix)\033[0m\n"
	@uv run --isolated ruff check --fix . |& tee reports/ruff-check.log | sed -E '/^Installed [0-9]+ packages in /d'
	# Markdown auto-fix (uses local Node via npx)
	@printf "\033[1;36m==> Markdownlint (fix)\033[0m\n"
	@npx --yes markdownlint-cli2 --fix |& tee reports/markdownlint.log || true
	# Type checking (mypy-baseline ratchet)
	@printf "\033[1;36m==> Type check (mypy-baseline)\033[0m\n"
	@bash .github/scripts/ci/type_check.sh
	# Complexity check (xenon baseline ratchet)
	@printf "\033[1;36m==> Complexity check (xenon-baseline)\033[0m\n"
	@bash .github/scripts/ci/complexity_check.sh --profile prod
	# Tests complexity (thresholds only, relaxed)
	@printf "\033[1;36m==> Complexity check (tests, relaxed)\033[0m\n"
	@bash .github/scripts/ci/complexity_check.sh --profile tests |& tee reports/xenon-tests.txt | sed -E '/^Installed [0-9]+ packages in /d'
	# Validate secrets against baseline using pre-commit hook across tracked files (excluding baselines)
	@printf "\033[1;36m==> Secrets scan (detect-secrets pre-commit hook)\033[0m\n"
	@bash -c 'FILES=$(git ls-files | grep -v "^\\.baseline-"); uv run --isolated python -m detect_secrets.pre_commit_hook --baseline .baseline-secrets -- $FILES' |& tee reports/detect-secrets.log | sed -E '/^Installed [0-9]+ packages in /d'
	# Tests (quiet console output, full log available)
	@printf "\033[1;36m==> Pytest\033[0m\n"
	@uv run --isolated pytest -q --color=yes --durations=10 --junitxml=reports/pytest-junit.xml |& tee reports/pytest.log | sed -E '/^Installed [0-9]+ packages in /d'

update:
	# Update all baselines in one go
	bash .github/scripts/ci/update_type_baseline.sh
	# Production complexity baseline (magent2 + scripts)
	bash .github/scripts/ci/update_complexity_baseline.sh --profile prod
	# Tests complexity baseline (tests only, relaxed thresholds)
	bash .github/scripts/ci/update_complexity_baseline.sh --profile tests
	uv run --isolated detect-secrets scan --exclude-files '^\\.baseline-.*$' > .baseline-secrets

test:
	uv run --isolated pytest -q

up +args:
	docker compose up -d {{args}}

down +args:
	docker compose down {{args}}

rebuild:
	docker compose down
	docker compose build
	docker compose up -d

# ──────────────────────────────────────────────────────────────────────────────
# Dev UX: Logs
# ──────────────────────────────────────────────────────────────────────────────
log:
	# Open LazyDocker for an interactive logs view across services
	@if command -v lazydocker >/dev/null 2>&1; then \
	  printf "\033[1;36m==> Launching LazyDocker\033[0m\n"; \
	  lazydocker; \
	else \
	  printf "\033[1;31mLazyDocker is not installed.\033[0m\n"; \
	  printf "Install instructions: https://github.com/jesseduffield/lazydocker#installation\n"; \
	  printf "Example (Linux):\n  curl -fsSL https://raw.githubusercontent.com/jesseduffield/lazydocker/master/scripts/install_update_linux.sh | bash\n"; \
	  exit 127; \
	fi

logs-pretty:
	# Pretty/color stream of all service logs using humanlog
	@if command -v humanlog >/dev/null 2>&1; then \
	  printf "\033[1;36m==> Streaming logs via humanlog\033[0m\n"; \
	  docker compose logs -f --no-log-prefix | humanlog; \
	else \
	  printf "\033[1;31mhumanlog is not installed.\033[0m\n"; \
	  printf "Install: https://humanlog.io/docs/integrations/structured-logging\n"; \
	  printf "Alternative: pipe via jq ->  docker compose logs -f --no-log-prefix | jq -C .\n"; \
	  exit 127; \
	fi
