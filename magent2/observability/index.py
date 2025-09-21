from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _enabled() -> bool:
    raw = (os.getenv("OBS_INDEX_ENABLED") or "").strip()
    if not raw:
        return True
    return raw not in {"0", "false", "False", "no"}


def _ttl_seconds() -> int:
    raw = (os.getenv("OBS_INDEX_TTL_DAYS") or "").strip()
    try:
        days = int(raw) if raw else 7
        return max(1, days) * 24 * 60 * 60
    except Exception:
        return 7 * 24 * 60 * 60


def _cap_recent_set(client: Any, key: str, cap: int) -> None:
    try:
        # If set size > cap, remove arbitrary members to bound size (best-effort)
        size = int(client.scard(key))
        if size > cap:
            excess = size - cap
            members: Iterable[Any] = client.srandmember(key, excess)
            if members:
                client.srem(key, *members)
    except Exception:
        pass


@dataclass
class ObserverIndex:
    client: Any | None

    @classmethod
    def from_bus(cls, bus: Any) -> ObserverIndex:
        try:
            from magent2.bus.redis_adapter import RedisBus  # local import to avoid hard dep

            if isinstance(bus, RedisBus):
                return cls(client=bus.get_client())
        except Exception:
            pass
        return cls(client=None)

    def is_active(self) -> bool:
        return _enabled() and self.client is not None

    # --- Writes ---
    def record_user_message(
        self, conversation_id: str, sender: str, recipient: str, text: str | None, ts_ms: int | None
    ) -> None:
        if not self.is_active():
            return
        cid = str(conversation_id)
        sender = str(sender)
        recipient = str(recipient)
        ts = int(ts_ms if ts_ms is not None else _now_ms())
        c = self.client
        if c is None:
            return
        ttl = _ttl_seconds()
        try:
            pipe = c.pipeline(transaction=False)
            # zset of conversations by last activity
            pipe.zadd("obs:conv:z", {cid: ts})
            # hash with basic metadata
            hkey = f"obs:conv:{cid}:h"
            pipe.hset(
                hkey,
                mapping={
                    "last_activity_ms": ts,
                    "last_sender": sender,
                    "last_recipient": recipient,
                },
            )
            pipe.hincrby(hkey, "msg_count", 1)
            # participants set
            pkey = f"obs:conv:{cid}:participants"
            pipe.sadd(pkey, sender)
            pipe.sadd(pkey, recipient)
            # edges hash
            ekey = f"obs:conv:{cid}:edges"
            pipe.hincrby(ekey, f"{sender}|{recipient}", 1)
            # TTLs (best-effort)
            pipe.expire(hkey, ttl)
            pipe.expire(pkey, ttl)
            pipe.expire(ekey, ttl)
            pipe.execute()
        except Exception:
            # best-effort only
            pass

    def record_run_started(self, agent_name: str, conversation_id: str, ts_ms: int | None) -> None:
        if not self.is_active():
            return
        name = str(agent_name)
        cid = str(conversation_id)
        ts = int(ts_ms if ts_ms is not None else _now_ms())
        c = self.client
        if c is None:
            return
        ttl = _ttl_seconds()
        try:
            pipe = c.pipeline(transaction=False)
            pipe.zadd("obs:agents:z", {name: ts})
            hkey = f"obs:agent:{name}:h"
            pipe.hset(hkey, mapping={"last_seen_ms": ts, "last_started_ms": ts})
            pipe.hincrby(hkey, "active_runs", 1)
            skey = f"obs:agent:{name}:convs"
            pipe.sadd(skey, cid)
            pipe.expire(hkey, ttl)
            pipe.expire(skey, ttl)
            pipe.execute()
            _cap_recent_set(c, skey, 50)
        except Exception:
            pass

    def record_run_completed(
        self, agent_name: str, conversation_id: str, ts_ms: int | None, *, errored: bool
    ) -> None:
        if not self.is_active():
            return
        name = str(agent_name)
        cid = str(conversation_id)
        ts = int(ts_ms if ts_ms is not None else _now_ms())
        c = self.client
        if c is None:
            return
        ttl = _ttl_seconds()
        try:
            pipe = c.pipeline(transaction=False)
            pipe.zadd("obs:agents:z", {name: ts})
            hkey = f"obs:agent:{name}:h"
            pipe.hset(hkey, mapping={"last_seen_ms": ts, "last_completed_ms": ts})
            # decrement active_runs but not below zero
            try:
                raw_active = c.hget(hkey, "active_runs")
                if isinstance(raw_active, (bytes | bytearray)):
                    active = int((raw_active or b"0").decode() or "0")
                elif raw_active is None:
                    active = 0
                else:
                    active = int(raw_active)
            except Exception:
                active = 0
            new_val = max(0, active - 1)
            pipe.hset(hkey, mapping={"active_runs": new_val})
            skey = f"obs:agent:{name}:convs"
            pipe.sadd(skey, cid)
            pipe.expire(hkey, ttl)
            pipe.expire(skey, ttl)
            pipe.execute()
            _cap_recent_set(c, skey, 50)
        except Exception:
            pass

    # --- Reads ---
    def _process_conversation_data(self, c: Any, cid: str) -> dict[str, Any]:
        """Process conversation data from Redis."""
        hkey = f"obs:conv:{cid}:h"
        data = c.hgetall(hkey)
        lam = data.get(b"last_activity_ms", b"0")
        last_activity_ms = int(
            (lam or b"0").decode() if isinstance(lam, (bytes | bytearray)) else str(lam)
        )
        mc = data.get(b"msg_count", b"0")
        msg_count = int((mc or b"0").decode() if isinstance(mc, (bytes | bytearray)) else str(mc))
        pkey = f"obs:conv:{cid}:participants"
        pcount = int(c.scard(pkey) or 0)
        return {
            "id": cid,
            "last_activity_ms": last_activity_ms,
            "participants_count": pcount,
            "msg_count": msg_count,
        }

    def list_conversations(
        self, limit: int = 50, since_ms: int | None = None
    ) -> list[dict[str, Any]]:
        if not self.is_active():
            return []
        c = self.client
        if c is None:
            return []
        try:
            if since_ms is not None:
                ids = c.zrevrangebyscore(
                    "obs:conv:z", "+inf", int(since_ms), start=0, num=int(limit)
                )
            else:
                ids = c.zrevrange("obs:conv:z", 0, int(limit) - 1)
            out: list[dict[str, Any]] = []
            for raw in ids:
                cid = raw.decode() if isinstance(raw, (bytes | bytearray)) else str(raw)
                conv_data = self._process_conversation_data(c, cid)
                out.append(conv_data)
            return out
        except Exception:
            return []

    def _decode_bytes(self, value: Any) -> str:
        if isinstance(value, (bytes | bytearray)):
            try:
                return value.decode()
            except Exception:
                return ""
        return str(value)

    def _parse_int(self, value: Any) -> int:
        text = self._decode_bytes(value) if value is not None else "0"
        try:
            return int(text or "0")
        except Exception:
            return 0

    def _agent_summary(self, c: Any, name: str) -> dict[str, Any]:
        h = c.hgetall(f"obs:agent:{name}:h")
        last_seen_ms = self._parse_int(h.get(b"last_seen_ms", b"0"))
        active_runs = self._parse_int(h.get(b"active_runs", b"0"))
        convs = c.smembers(f"obs:agent:{name}:convs")
        conv_ids = [self._decode_bytes(x) for x in convs][:50]
        return {
            "name": name,
            "last_seen_ms": last_seen_ms,
            "active_runs": active_runs,
            "recent_conversations": conv_ids,
        }

    def list_agents(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.is_active():
            return []
        c = self.client
        if c is None:
            return []
        try:
            names = c.zrevrange("obs:agents:z", 0, int(limit) - 1)
            return [self._agent_summary(c, self._decode_bytes(raw)) for raw in names]
        except Exception:
            return []

    def _extract_nodes(self, c: Any, cid: str) -> list[dict[str, str]]:
        """Extract participant nodes from Redis."""
        pkey = f"obs:conv:{cid}:participants"
        parts = c.smembers(pkey)
        nodes = []
        for raw in parts:
            pid = raw.decode() if isinstance(raw, (bytes | bytearray)) else str(raw)
            ntype = (
                "agent"
                if pid.startswith("agent:")
                else ("user" if pid.startswith("user:") else "other")
            )
            nodes.append({"id": pid, "type": ntype})
        return nodes

    def _extract_edges(self, c: Any, cid: str) -> list[dict[str, Any]]:
        """Extract conversation edges from Redis."""
        ekey = f"obs:conv:{cid}:edges"
        edges_raw = c.hgetall(ekey)
        edges = []
        for key_raw, val_raw in edges_raw.items():
            pair = key_raw.decode() if isinstance(key_raw, (bytes | bytearray)) else str(key_raw)
            val_text = (
                val_raw.decode() if isinstance(val_raw, (bytes | bytearray)) else str(val_raw)
            )
            try:
                count = int(val_text)
            except Exception:
                count = 0
            if "|" in pair:
                frm, to = pair.split("|", 1)
                edges.append({"from": frm, "to": to, "count": count})
        return edges

    def get_graph(self, conversation_id: str) -> dict[str, Any] | None:
        if not self.is_active():
            return {"nodes": [], "edges": []}
        c = self.client
        if c is None:
            return {"nodes": [], "edges": []}
        cid = str(conversation_id)
        try:
            nodes = self._extract_nodes(c, cid)
            edges = self._extract_edges(c, cid)
            return {"nodes": nodes, "edges": edges}
        except Exception:
            return {"nodes": [], "edges": []}

    def conversation_exists(self, conversation_id: str) -> bool:
        if not self.is_active():
            return False
        c = self.client
        if c is None:
            return False
        try:
            cid = str(conversation_id)
            return bool(int(c.exists(f"obs:conv:{cid}:h") or 0))
        except Exception:
            return False
