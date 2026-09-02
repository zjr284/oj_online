"""提交频率限制（api.md：1 分钟内提交超过 3 次 → 429）。

滑动窗口的单进程内存实现，对本课程规模足够；
将来若多进程部署，需替换为 Redis 等共享存储。
"""
import time
from collections import defaultdict, deque

from app.core.errors import ApiError


class RateLimiter:
    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        """超限时抛出 429。asyncio 单线程内调用，无需加锁。"""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise ApiError(429, "too many requests, please try later")
        q.append(now)
