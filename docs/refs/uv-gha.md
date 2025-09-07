# uv + GitHub Actions (CI)

- Use uv to install/sync; cache uv downloads/venv to speed builds.
- Cancel in‑progress jobs per branch to reduce churn; emit JUnit/JSON reports as artifacts.
- Keep CI steps: ruff check/format (dry run), mypy, pytest (unit/e2e), secrets scan.

## Example (GHA steps with uv)

```yaml
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/uv-action@v1
      - run: uv sync
      - run: uv run ruff check
      - run: uv run mypy
      - run: uv run pytest -q
```

## References

- uv + GitHub Actions guide; GHA caching docs
