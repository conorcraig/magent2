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
	@bash -c 'FILES=$(git ls-files | grep -v "^\\.baseline-"); uv run --isolated python -m detect_secrets.pre_commit_hook --baseline .baseline-secrets -- $$FILES' |& tee reports/detect-secrets.log | sed -E '/^Installed [0-9]+ packages in /d'
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
log-raw:
	# Stage 1: Raw Docker logs (no filtering)
	docker compose logs -f --no-log-prefix gateway worker

log:
    #!/usr/bin/env bash
    # Stage 4: Final human-readable output (filtered)
    if ! command -v jq >/dev/null 2>&1; then
      printf "\033[1;31mjq is not installed.\033[0m\n";
      printf "Install: https://jqlang.github.io/jq/download/\n";
      exit 127;
    fi
    if ! command -v humanlog >/dev/null 2>&1; then
      printf "\033[1;31mhumanlog is not installed.\033[0m\n";
      printf "Install: https://humanlog.io/docs/integrations/structured-logging\n";
      printf "Alternative: pipe via jq ->  docker compose logs -f --no-log-prefix | jq -C .\n";
      exit 127;
    fi
    docker compose logs -f --no-log-prefix gateway worker \
      | jq --unbuffered -Rc '
        . as $raw
        | (try fromjson catch $raw) as $j
        | if ($j|type)=="object" then $j else {msg:($j|tostring), level:"text"} end
        | .service = (.service // env.SERVICE_NAME // "unknown")
        | .msg = (.msg // .message // .log // .event // .err // "" | tostring)
        | select((.msg // "" | test("GET /health")) | not)
        | .msg = (if .service then ("[" + .service + "] " + .msg) else .msg end)
        | if has("service") then del(.service) else . end
      ' \
      | humanlog --time-format "15:04:05"

lazydocker:
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


# ──────────────────────────────────────────────────────────────────────────────
# Local (no Docker): gateway + worker in one process
# ──────────────────────────────────────────────────────────────────────────────
local:
	# Run FastAPI gateway (port 8000) and worker using an in-process bus
	uv run python scripts/run_local.py

# ──────────────────────────────────────────────────────────────────────────────
# Local (3 processes): Redis + Gateway + Worker
# ──────────────────────────────────────────────────────────────────────────────
redis:
	# Start a local Redis server in foreground
	@if command -v redis-server >/dev/null 2>&1; then \
	  echo "Starting redis-server on 6379"; \
	  redis-server --port 6379; \
	else \
	  echo "redis-server not found. Install via your package manager."; \
	  exit 127; \
	fi

gateway:
	# Run FastAPI gateway bound to 0.0.0.0:8000
	SERVICE_NAME=gateway REDIS_URL=${REDIS_URL:-redis://localhost:6379/0} \
	uv run uvicorn magent2.gateway.asgi:app --host 0.0.0.0 --port 8000 \
	  --log-level info --no-access-log

worker:
	# Run the worker loop (EchoRunner if no OPENAI_API_KEY)
	SERVICE_NAME=worker REDIS_URL=${REDIS_URL:-redis://localhost:6379/0} \
	uv run python -m magent2.worker

# ──────────────────────────────────────────────────────────────────────────────
# Local stack orchestration (single terminal): start/stop all 3 services
# ──────────────────────────────────────────────────────────────────────────────
stack-up:
	# Start Redis, Gateway, and Worker in background with PID files
	set -eu
	# Clean up any previous PIDs/processes (best-effort)
	pkill -f "python -m magent2.worker" >/dev/null 2>&1 || true
	pkill -f "uvicorn magent2.gateway.asgi:app" >/dev/null 2>&1 || true
	if command -v redis-cli >/dev/null 2>&1; then redis-cli -p 6379 shutdown >/dev/null 2>&1 || true; fi
	rm -f .devstack/redis.pid .devstack/gateway.pid .devstack/worker.pid
	mkdir -p .devstack
	# Start Redis
	if ! command -v redis-server >/dev/null 2>&1; then \
	  echo "redis-server not found. Install it or run: sudo apt-get install -y redis-server"; \
	  exit 127; \
	fi
	nohup redis-server --port 6379 --save "" --appendonly no >/tmp/redis-local.log 2>&1 & echo $! > .devstack/redis.pid
	# Wait for Redis ping
	for i in $(seq 1 100); do if command -v redis-cli >/dev/null 2>&1 && redis-cli -p 6379 ping >/dev/null 2>&1; then break; else sleep 0.05; fi; done
	# Start Gateway
	SERVICE_NAME=gateway REDIS_URL=${REDIS_URL:-redis://localhost:6379/0} nohup ./.venv/bin/python -m uvicorn magent2.gateway.asgi:app --host 0.0.0.0 --port 8000 --log-level info --no-access-log >/tmp/gateway.log 2>&1 & echo $! > .devstack/gateway.pid
	# Wait for Gateway /health
	for i in $(seq 1 200); do if curl -sfS http://localhost:8000/health >/dev/null 2>&1; then break; else sleep 0.05; fi; done
	# Start Worker (no consumer groups for local convenience)
	SERVICE_NAME=worker REDIS_URL=${REDIS_URL:-redis://localhost:6379/0} WORKER_USE_GROUPS=${WORKER_USE_GROUPS:-0} AGENT_NAME=${AGENT_NAME:-DevAgent} nohup ./.venv/bin/python -m magent2.worker >/tmp/worker.log 2>&1 & echo $! > .devstack/worker.pid
	printf "Stack is up (Redis:6379, Gateway:8000, Worker).\n"

stack-down:
	# Stop Worker, Gateway, and Redis (best-effort)
	set -eu
	# Kill by pid files if present
	for svc in worker gateway redis; do \
	  if [ -f .devstack/${svc}.pid ]; then \
	    pid=$(cat .devstack/${svc}.pid || true); \
	    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then \
	      kill "$pid" >/dev/null 2>&1 || true; \
	    fi; \
	    rm -f .devstack/${svc}.pid; \
	  fi; \
	done
	# Fallback: kill by process patterns (best-effort)
	pkill -f "python -m magent2.worker" >/dev/null 2>&1 || true
	pkill -f "uvicorn magent2.gateway.asgi:app" >/dev/null 2>&1 || true
	# Graceful Redis shutdown if CLI available
	if command -v redis-cli >/dev/null 2>&1; then redis-cli -p 6379 shutdown >/dev/null 2>&1 || true; fi
	printf "Stack is down.\n"
