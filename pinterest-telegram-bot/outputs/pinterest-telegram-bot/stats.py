from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class StatsSummary:
    total_users: int
    active_24h: int
    active_7d: int
    total_requests: int
    successful_requests: int
    requests_24h: int
    requests_7d: int
    media_files: int
    platform_successes: dict[str, int]
    collecting_since: str

    @property
    def success_rate(self) -> float:
        return self.successful_requests / self.total_requests * 100 if self.total_requests else 0.0


class StatsStore:
    """Process-local statistics. Data resets whenever the service restarts."""

    def __init__(self) -> None:
        self._users: dict[int, tuple[int, int]] = {}
        self._events: list[tuple[int, str, bool, int]] = []
        self._started_at = int(time.time())
        self._lock = asyncio.Lock()

    async def touch_user(self, user_id: int) -> None:
        if user_id <= 0:
            return
        now = int(time.time())
        async with self._lock:
            first_seen, _ = self._users.get(user_id, (now, now))
            self._users[user_id] = (first_seen, now)

    async def record(self, user_id: int, platform: str, success: bool, media_count: int = 0) -> None:
        now = int(time.time())
        async with self._lock:
            if user_id > 0:
                first_seen, _ = self._users.get(user_id, (now, now))
                self._users[user_id] = (first_seen, now)
            self._events.append((now, platform, success, max(0, media_count)))

    async def summary(self) -> StatsSummary:
        now = int(time.time())
        since_24h = now - 86400
        since_7d = now - 604800
        async with self._lock:
            users = dict(self._users)
            events = list(self._events)
        successful = [event for event in events if event[2]]
        platforms: dict[str, int] = {}
        for _, platform, _, _ in successful:
            platforms[platform] = platforms.get(platform, 0) + 1
        return StatsSummary(
            total_users=len(users),
            active_24h=sum(last_seen >= since_24h for _, last_seen in users.values()),
            active_7d=sum(last_seen >= since_7d for _, last_seen in users.values()),
            total_requests=len(events),
            successful_requests=len(successful),
            requests_24h=sum(created_at >= since_24h for created_at, *_ in events),
            requests_7d=sum(created_at >= since_7d for created_at, *_ in events),
            media_files=sum(media_count for _, _, _, media_count in successful),
            platform_successes=dict(sorted(platforms.items())),
            collecting_since=datetime.fromtimestamp(self._started_at, tz=timezone.utc).strftime("%d.%m.%Y"),
        )

