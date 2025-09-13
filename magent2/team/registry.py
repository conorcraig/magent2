from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field


@dataclass(slots=True)
class AgentRecord:
    agent_name: str
    team_name: str
    responsibilities: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)


class TeamRegistry:
    """In-memory registry for teams, agents, responsibilities, and file scopes.

    - Tracks per-team window person (escalation contact)
    - Tracks agents with their responsibilities and allowed file path patterns
    - Optionally records a per-agent worktree directory

    Path ownership resolution uses longest-prefix/most-specific glob match semantics.
    """

    def __init__(self) -> None:
        self._window_person_by_team: dict[str, str] = {}
        self._agents_by_name: dict[str, AgentRecord] = {}

    # ----------------------------
    # Team/window person
    # ----------------------------
    def set_window_person(self, team_name: str, person: str) -> None:
        self._window_person_by_team[team_name] = person

    def get_window_person(self, team_name: str) -> str | None:
        return self._window_person_by_team.get(team_name)

    # ----------------------------
    # Agent records
    # ----------------------------
    def register_agent(
        self,
        *,
        team_name: str,
        agent_name: str,
        responsibilities: list[str] | None = None,
        allowed_paths: list[str] | None = None,
    ) -> AgentRecord:
        record = AgentRecord(
            agent_name=agent_name,
            team_name=team_name,
            responsibilities=list(responsibilities or []),
            allowed_paths=[_normalize_path(p) for p in (allowed_paths or [])],
        )
        self._agents_by_name[agent_name] = record
        return record

    def update_agent(
        self,
        agent_name: str,
        *,
        responsibilities: list[str] | None = None,
        allowed_paths: list[str] | None = None,
    ) -> AgentRecord:
        record = self._require_agent(agent_name)
        if responsibilities is not None:
            record.responsibilities = list(responsibilities)
        if allowed_paths is not None:
            record.allowed_paths = [_normalize_path(p) for p in allowed_paths]
        return record

    def get_agent(self, agent_name: str) -> AgentRecord | None:
        return self._agents_by_name.get(agent_name)

    def list_team_agents(self, team_name: str) -> list[AgentRecord]:
        return [r for r in self._agents_by_name.values() if r.team_name == team_name]

    # ----------------------------
    # Ownership resolution
    # ----------------------------
    def find_owner_for_path(self, path: str) -> AgentRecord | None:
        """Return the AgentRecord that most specifically owns a given path.

        Resolution rules:
        - Normalize both candidate path and patterns to POSIX-ish forward slashes
        - Support simple glob patterns in allowed_paths (e.g., "src/app/**", "README.md")
        - If multiple agents match, choose the one with the longest matching pattern
        - If tie, return one deterministically by agent_name ordering
        """
        target = _normalize_path(path)
        matches: list[tuple[int, AgentRecord]] = []
        for record in self._agents_by_name.values():
            for pat in record.allowed_paths:
                if _glob_match(target, pat):
                    matches.append((len(pat), record))
        if not matches:
            return None
        # Sort by pattern length desc, then by agent_name for deterministic tie-break
        matches.sort(key=lambda t: (-t[0], t[1].agent_name))
        return matches[0][1]

    # ----------------------------
    # Internals
    # ----------------------------
    def _require_agent(self, agent_name: str) -> AgentRecord:
        rec = self._agents_by_name.get(agent_name)
        if rec is None:
            raise KeyError(f"unknown agent: {agent_name}")
        return rec


_REGISTRY_SINGLETON: TeamRegistry | None = None


def get_registry() -> TeamRegistry:
    global _REGISTRY_SINGLETON
    if _REGISTRY_SINGLETON is None:
        _REGISTRY_SINGLETON = TeamRegistry()
        # Optional default window person from env for quick-starts
        default_team = os.getenv("TEAM_NAME_DEFAULT", "Team").strip()
        default_window = os.getenv("TEAM_WINDOW_PERSON", "").strip()
        if default_window:
            _REGISTRY_SINGLETON.set_window_person(default_team, default_window)
    return _REGISTRY_SINGLETON


def reset_registry_for_testing() -> None:
    global _REGISTRY_SINGLETON
    _REGISTRY_SINGLETON = TeamRegistry()


def _normalize_path(p: str) -> str:
    # Normalize to forward slashes and remove leading './'
    s = p.replace("\\", "/").strip()
    if s.startswith("./"):
        s = s[2:]
    return s


def _glob_match(path: str, pattern: str) -> bool:
    # Support simple ** and * patterns via fnmatch with forward slashes
    # Ensure both are normalized
    p = _normalize_path(path)
    pat = _normalize_path(pattern)
    # Make "dir" pattern match "dir/**"
    candidates: list[str] = [pat]
    if not any(ch in pat for ch in ["*", "?", "["]):
        if not pat.endswith("/"):
            candidates.append(pat + "/**")
        else:
            candidates.append(pat + "**")
    return any(fnmatch.fnmatch(p, c) for c in candidates)


__all__ = [
    "AgentRecord",
    "TeamRegistry",
    "get_registry",
    "reset_registry_for_testing",
]

