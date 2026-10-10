import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class RateLimiter:
    """
    Simple in-memory limiter: at most `limit` hits per `window_seconds` for a
    given key. Counts reset if the server restarts, which is fine for stopping
    password guessing and signup spam on a single server.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please wait a few minutes and try again.",
            )
        hits.append(now)

        # Keep memory bounded: drop keys whose hits have all expired.
        if len(self._hits) > 5000:
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > window_seconds]:
                del self._hits[k]

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    # Behind Render's proxy the real visitor address is in X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
