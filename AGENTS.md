# Agent Working Agreement (AGENTS.md)

## Purpose

- **Goal**: Define exactly how the AI assistant works in this repository: what it must do, must not do, and the expected workflow and standards.

## Core Principles

- **Requirements-driven**: Always read the PRD.md file BEFORE beginning any work so that you have proper context.
- **User-first**: Execute the user's request end-to-end without unnecessary back-and-forth. Ask clarifying questions only when instructions are ambiguous or blocked.
- **Surgical changes**: Make minimal, targeted edits. Do not reformat unrelated code or introduce speculative abstractions.
- **TDD + correctness**: Prefer tests-first where practical. Respect existing tests, fixtures, and `conftest.py`. Fix failures before finishing.
- **Security**: Never reveal or access secrets. Treat environment files as sensitive.
- **Determinism**: Avoid trial-and-error; reason through fixes locally before pushing work upstream.

## MUST

- **Maintain a task list**: For multi-step work, maintain an internal todo list with clear statuses; mark steps completed as soon as they are done.
- **Status updates**: Provide brief progress notes during work, and a short high-signal summary at completion WITHOUT ENDING YOUR TURN, JUST INCLUDE IT IN TOKEN STREAM.
- **Understand before editing**:
  - Use semantic search to explore the codebase for how things work.
  - Use exact-text searches for symbols when needed.
  - Read relevant files before editing anything.
- **Follow local tooling**:
  - Use `uv` for Python dependency management (`uv add`, `uv remove`).
  - Do not manually edit `pyproject.toml` for dependencies.
  - Prefer GitHub CLI (`gh`) for repo tasks; fall back to built-in integrations if needed.
- **Testing and linting**:
  - Run only the relevant tests for changed areas; run the whole suite only when appropriate.
  - Respect and run pre-commit hooks on staged files (not the entire repo unless requested). UNDER NO CIRCUMSTANCES CAN YOU SKIP THIS.
  - BEFORE EVEN ATTEMPTING A COMMIT, RUN `just check`. ONLY COMMIT IF GREEN.
  - Fix lints and type errors instead of suppressing or ignoring them.
- **Edits and formatting**:
  - Preserve the existing indentation style and width.
  - Keep unrelated formatting unchanged.
  - Use clear naming, docstrings for core code, and explicit types where applicable.
- **Terminal hygiene**:
  - Use absolute paths in commands where possible.
  - Prefer simple, non-chained commands; avoid unnecessary `cd`.
  - Use non-interactive flags (e.g., `--yes`) and pipe pagers to `cat`.
  - When waiting in terminal, use `sleep` rather than pausing work.
- **File operations**: Use `git mv` for moves/renames; do not manipulate tracked files outside Git when changing their paths.
- **External research**: For external tooling/library issues or unexpected third-party behavior, consult official docs and credible sources before changes.

## MUST NOT

- **Secrets**:
  - Do not open, print, or display contents of secret files such as `*.env`, `*.pem`, `*.key`.
  - Do not log or echo secrets under any circumstances.
- **Dependency management**:
  - Do not use `pip` or `uv pip` for dependency changes.
  - Do not use Poetry for this project.
  - Do not manually edit dependency files; use `uv` subcommands instead.
- **Linting and tests**:
  - Do not add ignore comments or disable rules to “pass” checks. Fix root causes.
  - Do not skip tests unless the user explicitly agrees and it is justified.
- **Editing**:
  - Do not introduce TODO placeholders or commented-out code; implement the needed behavior cleanly.
  - Do not reformat unrelated files or change indentation styles.
  - Do not mass-rewrite files; prefer minimal diffs.
- **Process**:
  - Do not push trial-and-error to CI. Diagnose locally first.
  - Do not revert or reset large areas of code without explicit user approval.
  - Do not run pre-commit across the entire repo just to validate staged changes.
  - Do not create PR via gh cli, let user create it for you.

## Workflow Expectations

- **1) Discovery**
  - Clarify the goal if ambiguous. Otherwise proceed.
  - Search the codebase semantically to map where logic lives; use exact-text search for symbols.
  - For multi-step tasks, create and update a short todo list and start with the first item.

- **2) Implement**
  - Make focused edits using the repository’s existing patterns.
  - Add concise docstrings for core functions/classes; prefer explicit types in public APIs.
  - Keep changes minimal and readable; avoid deep nesting and unused abstractions.

- **3) Validate**
  - Run relevant tests; fix failures.
  - Run linting and type checks; fix issues rather than silencing them.
  - If hooks are configured, run pre-commit on staged files.

- **4) Communicate**
  - Provide short status updates during work and a concise end-of-task summary highlighting impactful changes.
  - If blocked, state the blocker and the exact information needed to proceed.

## Coding Standards (Python)

- **Naming**: Use meaningful names (functions as verbs, variables as nouns). Avoid cryptic abbreviations.
- **Types**: Prefer explicit, safe typing for public APIs. Avoid `Any` unless necessary.
- **Control flow**: Use guard clauses; avoid deep nesting; handle edge cases early; meaningful error handling only.
- **Comments/Docs**: Keep comments minimal and focused on “why”. Add docstrings for core components.
- **Formatting**: Prefer multi-line clarity over dense one-liners; do not reformat unrelated code.

## Tools Conventions

- **Search**: Prefer semantic search for understanding, exact search for symbols.
- **Parallelism**: Where safe, batch independent read-only operations in parallel to improve speed.
- **Commands**: Use non-interactive flags; avoid long-running foreground jobs. If needed, run jobs in the background and wait with `sleep`.

## Exceptions

- If the user explicitly instructs an exception to a rule (e.g., “skip creating an issue for this change”), follow the instruction for that instance and continue.

## Local Preferences Snapshot

- **Dependency manager**: `uv` (not Poetry). Use `uv add/remove`, never `pip` or `uv pip` for dependency changes.
- **GitHub**: Prefer `gh` CLI; close issues you open once work is complete, unless the user says otherwise.
- **Issue ownership**: All handover issues are assigned to `@conorcraig`. To claim an issue, add a top-level comment saying `claim` before starting work. Always read the latest comments to ensure it is not already claimed.
- **Pre-commit**: Present; run on staged files only.
- **Testing**: TDD-oriented; reuse fixtures; read `conftest.py`.
- **Coding style**: Minimal, YAGNI; no commented-out or TODO code; preserve indentation and avoid unrelated reformatting.
- **Security**: Never display `.env` or other secrets.
- **Terminal**: Prefer absolute paths; simple commands; use `sleep` for waits.

## Environment Setup

- Preferred: run the setup script
  - This repository includes a non-interactive, user-space setup script that installs `uv`, installs `gh` into `$HOME/.local/bin`, authenticates `gh` using `GH_TOKEN`, and syncs Python dependencies if `pyproject.toml` exists.

```bash
bash scripts/setup_env.sh
```

- Manual GH CLI install and auth (Linux, user-space)
  - If you prefer to run the documented steps directly:

```bash
# Install gh in user space
VER=2.61.0
ARCH=$(case "$(uname -m)" in x86_64) echo amd64 ;; aarch64|arm64) echo arm64 ;; *) echo amd64 ;; esac)
curl -fsSL "https://github.com/cli/cli/releases/download/v${VER}/gh_${VER}_linux_${ARCH}.tar.gz" \
  | tar -xz --strip-components=2 -C "$HOME/.local/bin" gh_${VER}_linux_${ARCH}/bin/gh

# Verify
~/.local/bin/gh --version

# Cursor injects GH_TOKEN as secret
echo "$GH_TOKEN" | ~/.local/bin/gh auth login --with-token
~/.local/bin/gh auth status
```

- Policy for GitHub interactions
  - Use `gh` CLI by default for repo operations (issues, PRs, labels, etc.).
  - Fall back to direct HTTPS API requests only if `gh` is unavailable.

## GitHub CLI Workflow

### Investigating CI Failures

```bash
# Find open PRs
gh pr list --state open

# Check CI status for specific PRs
gh pr checks <pr_number>

# Get detailed CI failure logs
gh run list --limit 10
gh run view <run_id> --log-failed
```

### Working on PRs

```bash
# Checkout PR branches to work on them
gh pr checkout <pr_number>

# Make changes, then commit and push
git add <files>
git commit -m "descriptive message"
git push
```

### Monitoring CI Results

```bash
# Check CI status after pushing changes
gh pr checks <pr_number>

# Wait for CI to complete, then verify success
gh pr checks <pr_number>  # Should show all green
```

### Key Commands

- `gh pr list` - List PRs
- `gh pr checks <number>` - Check CI status for a specific PR
- `gh pr checkout <number>` - Switch to PR branch locally
- `gh run list` - List recent workflow runs
- `gh run view <run_id> --log-failed` - Get detailed failure logs

### Notes on dependencies

- The setup script runs `uv sync --group dev` so development dependencies are installed alongside default ones. If you want to install additional groups, append more flags, e.g. `--group test`.
