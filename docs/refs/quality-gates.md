# Quality gates (Ruff, mypy, secrets, complexity)

- Ruff: checks + formatter; keep config minimal and project‑wide.
- mypy: strict where feasible (no implicit Optional, disallow untyped defs); manage baselines for legacy code.
- detect‑secrets: maintain a baseline; run in pre‑commit and CI; never commit real secrets.
- Xenon/Radon: set complexity thresholds; fail CI when functions exceed target score; ratchet down over time.

## Examples

```toml
[tool.mypy]
disallow_untyped_defs = true
no_implicit_optional = true
```

```bash
uv run ruff check && uv run mypy && uv run pytest -q
```

## References

- Ruff docs; mypy docs; detect‑secrets; Xenon/Radon
