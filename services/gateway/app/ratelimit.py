"""Per-user rate limits (token bucket), so one member cannot exhaust shared upstreams or the LLM quota.

A bucket holds up to `per_minute` tokens and refills continuously at per_minute/60 tokens per second;
each request takes one token. Bursts up to the limit are allowed, the long-run rate is capped.

State lives in this process's memory: exact for one gateway replica. With N replicas each enforces
its own bucket (up to N x the limit); a shared store such as Redis would make it global.
"""

import os
import time


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.capacity = per_minute
        self.refill_per_second = per_minute / 60
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> tuple[bool, float]:
        """(allowed, seconds until the next token if not allowed). A limit of 0 disables limiting."""
        if self.capacity <= 0:
            return True, 0.0
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_second)
        if tokens >= 1:
            self._buckets[key] = (tokens - 1, now)
            return True, 0.0
        self._buckets[key] = (tokens, now)
        return False, (1 - tokens) / self.refill_per_second


tool_calls = RateLimiter(int(os.getenv("RATE_LIMIT_TOOL_CALLS_PER_MINUTE", "60")))
chat_messages = RateLimiter(int(os.getenv("RATE_LIMIT_CHAT_MESSAGES_PER_MINUTE", "10")))
