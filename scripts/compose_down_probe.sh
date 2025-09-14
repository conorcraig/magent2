#!/usr/bin/env bash
set -Euo pipefail

# Simple defaults - just run from current directory
PROJECT_DIR="$(pwd)"
MODE="down"
TIMEOUT="15"
WITH_VOLUMES=0
REMOVE_ORPHANS=0

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found in PATH" >&2
  exit 1
fi

# choose compose
if docker compose version >/dev/null 2>&1; then
  COMPOSE_BIN=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_BIN=(docker-compose)
else
  echo "docker compose or docker-compose not available" >&2
  exit 1
fi

LOG_DIR="$(mktemp -d /tmp/compose_probe.XXXXXX)"
MAIN_LOG="$LOG_DIR/probe.log"
EVENTS_LOG="$LOG_DIR/docker_events.log"
JOURNAL_LOG="$LOG_DIR/dockerd.log"
COMPOSE_LOG="$LOG_DIR/compose_${MODE}.log"

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$MAIN_LOG"
}

PIDS=()

start_watchers() {
  log "Starting docker events watcher"
  docker events --format '{{.Time}} {{.Type}} {{.Action}} {{.Actor.ID}} {{.Actor.Attributes.name}} {{json .Actor.Attributes}}' > "$EVENTS_LOG" 2>&1 &
  PIDS+=($!)

  if command -v journalctl >/dev/null 2>&1 && journalctl -u docker -n 1 >/dev/null 2>&1; then
    log "Starting dockerd journal watcher"
    journalctl -u docker -f > "$JOURNAL_LOG" 2>&1 &
    PIDS+=($!)
  else
    log "journalctl for dockerd not available; skipping"
  fi
}

stop_watchers() {
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" 2>/dev/null || true
    fi
  done
}

cleanup() {
  stop_watchers
  log "Log dir: $LOG_DIR"
}
trap cleanup EXIT

log "Log dir: $LOG_DIR"
log "Project dir: $PROJECT_DIR"
log "Compose bin: ${COMPOSE_BIN[*]}"
log "Mode: $MODE  timeout: $TIMEOUT  volumes: $WITH_VOLUMES  remove-orphans: $REMOVE_ORPHANS"
log "COMPOSE_PARALLEL_LIMIT=${COMPOSE_PARALLEL_LIMIT:-<unset>}"

pushd "$PROJECT_DIR" >/dev/null

log "Saving docker compose config"
{ "${COMPOSE_BIN[@]}" config; } > "$LOG_DIR/compose_config.yaml" 2>&1 || true

log "Saving pre-run ps and sizes"
{ "${COMPOSE_BIN[@]}" ps -a; } > "$LOG_DIR/pre_ps.txt" 2>&1 || true
docker ps --size > "$LOG_DIR/pre_docker_ps_size.txt" 2>&1 || true
docker system df -v > "$LOG_DIR/pre_system_df.txt" 2>&1 || true

log "Saving container StopSignal/StopTimeout"
CONTAINERS=($("${COMPOSE_BIN[@]}" ps -q || true))
if [[ ${#CONTAINERS[@]} -gt 0 ]]; then
  {
    for id in "${CONTAINERS[@]}"; do
      docker inspect "$id" --format 'Name={{.Name}} Image={{.Config.Image}} StopSignal={{.Config.StopSignal}} StopTimeout={{.StopTimeout}} Entrypoint={{.Config.Entrypoint}} Cmd={{.Config.Cmd}}'
    done
  } > "$LOG_DIR/pre_container_inspect.txt" 2>&1 || true
fi

start_watchers

compose_cmd() {
  local args=()
  case "$MODE" in
    down)
      [[ "$WITH_VOLUMES" -eq 1 ]] && args+=(--volumes)
      [[ "$REMOVE_ORPHANS" -eq 1 ]] && args+=(--remove-orphans)
      args+=(--timeout "$TIMEOUT")
      echo "DOCKER_CLIENT_DEBUG=1 ${COMPOSE_BIN[*]} --ansi never --verbose down ${args[*]}"
      DOCKER_CLIENT_DEBUG=1 "${COMPOSE_BIN[@]}" --ansi never --verbose down "${args[@]}"
      ;;
    stop)
      echo "DOCKER_CLIENT_DEBUG=1 ${COMPOSE_BIN[*]} --ansi never --verbose stop --timeout $TIMEOUT"
      DOCKER_CLIENT_DEBUG=1 "${COMPOSE_BIN[@]}" --ansi never --verbose stop --timeout "$TIMEOUT"
      ;;
    rm)
      echo "DOCKER_CLIENT_DEBUG=1 ${COMPOSE_BIN[*]} --ansi never --verbose rm -f"
      DOCKER_CLIENT_DEBUG=1 "${COMPOSE_BIN[@]}" --ansi never --verbose rm -f
      ;;
    stop-rm)
      echo "DOCKER_CLIENT_DEBUG=1 ${COMPOSE_BIN[*]} --ansi never --verbose stop --timeout $TIMEOUT"
      DOCKER_CLIENT_DEBUG=1 "${COMPOSE_BIN[@]}" --ansi never --verbose stop --timeout "$TIMEOUT"
      echo "DOCKER_CLIENT_DEBUG=1 ${COMPOSE_BIN[*]} --ansi never --verbose rm -f"
      DOCKER_CLIENT_DEBUG=1 "${COMPOSE_BIN[@]}" --ansi never --verbose rm -f
      ;;
    *)
      log "Unknown mode: $MODE"
      return 1
      ;;
  esac
}

start_ns=$(date +%s%N)
{
  compose_cmd
} > "$COMPOSE_LOG" 2>&1 || true
end_ns=$(date +%s%N)
duration_ms=$(( (end_ns - start_ns) / 1000000 ))
log "Compose $MODE duration_ms=$duration_ms (log: $COMPOSE_LOG)"

log "Saving post-run ps and sizes"
{ "${COMPOSE_BIN[@]}" ps -a; } > "$LOG_DIR/post_ps.txt" 2>&1 || true
docker ps --size > "$LOG_DIR/post_docker_ps_size.txt" 2>&1 || true
docker system df -v > "$LOG_DIR/post_system_df.txt" 2>&1 || true

popd >/dev/null

log "Quick next steps:"
log "1) grep -E 'stop|die|kill|destroy|disconnect' $EVENTS_LOG | tail -n +1 | head -n 50"
log "2) review $COMPOSE_LOG for client timings"
log "3) compare pre_/post_ files to see where time went"
