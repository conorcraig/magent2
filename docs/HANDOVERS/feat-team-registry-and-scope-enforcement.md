# Handover: Team registry and file‑scope enforcement

Owner: next agent
Tracking issue: <https://github.com/conorcraig/magent2/issues/84>

## Context

- PRD requires a registry of agents/roles/ownership and enforcement of allowed file scopes during tool operations.
- `TerminalTool` already supports a sandbox cwd and deny‑list, but there is no registry module or centralized file‑scope policy.

## Deliverables

1) Registry module (in‑memory + env loader):
   - `magent2/registry/models.py` — dataclasses/Pydantic models for `AgentInfo`, `FileScope`.
   - `magent2/registry/loader.py` — load from env (JSON) or simple files. Env example: `REGISTRY_JSON`.
   - `magent2/registry/api.py` — query helpers, e.g., `allowed_paths_for(agent_name: str) -> list[str]`.
2) Policy enforcement (initial):
   - Integrate a best‑effort check in `TerminalTool` to deny `cwd` outside allowed scopes when a registry is loaded. This complements sandbox support and remains opt‑in.
   - Provide a small helper `magent2/registry/enforce.py` with `assert_path_allowed(path, allowed_roots)` used by tools.
3) Tests for policy and loader behavior.

## File references

- New: `magent2/registry/{models.py,loader.py,api.py,enforce.py}`
- Existing: `magent2/tools/terminal/tool.py` (limited integration)
- Tests: `tests/test_registry_scope.py`

## Design

- Keep the registry minimal and JSON‑backed initially for offline operation.
- Allowed path semantics: roots (directories) under which edits are permitted; deny attempts to escape. Normalize and resolve symlinks before checks.
- Tools find the registry via `REGISTRY_JSON` or an explicit `REGISTRY_FILE` path in env; absence means “no restriction” beyond existing tool policy.

## Tests (TDD)

- Loader: valid/invalid JSON; precedence between `REGISTRY_JSON` and `REGISTRY_FILE`.
- Enforcement: allow within scope; deny outside; edge cases for `..` and symlinks.
- Terminal integration: when registry present + sandbox set, combining both still denies escapes.

## Acceptance criteria

- Attempts outside allowed scopes are denied with actionable errors.
- Unit tests pass; quality gates green (`just check`).

## Risks & mitigations

- Over‑restriction: make the registry opt‑in; document env variables and defaults.
- False positives with symlinks: resolve to real paths prior to checks.

## Branch and ownership

- Branch name: `feat/team-registry-scope`
- Ownership: new `magent2/registry/*` files + a small, isolated change in `TerminalTool`. Avoid touching unrelated modules.
