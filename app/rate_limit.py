"""In-memory sliding-window rate limit for POST /purchase and POST /lab/run.

Protects the free LLM quota. State lives in this process only, so it resets on
restart and is not shared between workers (the deployment runs one).
"""

import threading
import time
from collections import defaultdict, deque


BUCKET_NOUNS = {"purchase": "purchases", "lab": "lab runs"}


class PurchaseLimiter:
    def __init__(self, per_ip=10, per_ip_window=3600, overall=100, overall_window=86400, clock=time.monotonic):
        self.per_ip = per_ip
        self.per_ip_window = per_ip_window
        self.overall = overall
        self.overall_window = overall_window
        self.clock = clock
        self._lock = threading.Lock()
        self._by_ip = defaultdict(deque)
        self._all = deque()

    @staticmethod
    def _prune(events, now, window):
        while events and events[0] <= now - window:
            events.popleft()

    def check(self, ip, bucket="purchase") -> str | None:
        """Record an attempt; return a refusal message if over a limit, else None.

        Each bucket (purchases, lab runs) has its own per-IP limit; all buckets
        share the overall daily cap.
        """
        noun = BUCKET_NOUNS.get(bucket, "requests")
        with self._lock:
            now = self.clock()
            mine = self._by_ip[(bucket, ip)]
            self._prune(mine, now, self.per_ip_window)
            self._prune(self._all, now, self.overall_window)
            if len(mine) >= self.per_ip:
                return f"Too many {noun}: at most {self.per_ip} per hour from one address. Try again later."
            if len(self._all) >= self.overall:
                return f"The demo has reached its limit of {self.overall} runs per day. Try again tomorrow."
            mine.append(now)
            self._all.append(now)
            return None

    def reset(self):
        with self._lock:
            self._by_ip.clear()
            self._all.clear()


def client_ip(request) -> str:
    """First X-Forwarded-For entry when present (Render's proxy sets it), else the socket peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"
