# Handover Index (parallelizable workstreams)

This directory contains the single source of truth for current handovers. Each handover is designed to be executed on an isolated feature branch with minimal merge conflict risk. Implementers should not need external context beyond these docs and the referenced files.

## Parallel tracks

- Worker idle CPU reduction — `feat-worker-idle-backoff.md`
- Gateway SSE non-blocking offload — `feat-gateway-sse-offload.md`
- Signals v2 (wait_any/all, policy, SSE visibility) — `feat-signals-v2-policy-and-sse.md`
- Team registry and file-scope enforcement — `feat-team-registry-and-scope-enforcement.md`
- Git worktree allocator — `feat-worktree-allocator.md`
- Client v1 polish — `feat-client-v1-polish.md`
- Observability wiring v2 — `feat-observability-wiring-v2.md`
- Terminal redaction env — `feat-terminal-redaction-env.md`
- Ruff config tightening — `feat-ruff-config-tightening.md`
- Docs alignment — `feat-docs-alignment.md`

All previous handovers have been superseded. Do not use older files.