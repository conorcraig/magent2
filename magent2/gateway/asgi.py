from __future__ import annotations

import os

from magent2.bus.redis_adapter import RedisBus

from .app import create_app

_bus = RedisBus(redis_url=os.getenv("REDIS_URL"))
app = create_app(_bus)
