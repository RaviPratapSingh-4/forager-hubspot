from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger("app")


class ApiError(Exception):
    def __init__(self, status: int, text: str, url: str = "") -> None:
        super().__init__(f"{status} {url} {text[:300]}")
        self.status, self.text, self.url = status, text, url


def retry_after(r: httpx.Response, default: float) -> float:
    try:
        return min(float(r.headers.get("Retry-After", default)), 30.0)
    except ValueError:
        return default


async def send(client: httpx.AsyncClient, method: str, url: str, retries: int = 4, **kw) -> httpx.Response:
    """Request with exponential backoff on 429 / 5xx / transport errors. Never raises on 4xx."""
    delay = 1.0
    r: httpx.Response | None = None
    for attempt in range(retries + 1):
        try:
            r = await client.request(method, url, **kw)
        except httpx.TransportError as e:
            if attempt == retries:
                raise ApiError(0, str(e), url) from e
            log.warning("transport error, retrying", extra={"url": url, "attempt": attempt, "error": f"{type(e).__name__}: {e}"})
            await asyncio.sleep(delay)
            delay *= 2
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries:
                return r
            wait = retry_after(r, delay)
            log.warning("retrying", extra={"url": url, "status": r.status_code, "wait": wait})
            await asyncio.sleep(wait)
            delay *= 2
            continue
        return r
    assert r is not None
    return r


def check(r: httpx.Response, ok: tuple[int, ...] = (200, 201, 202, 204)) -> None:
    if r.status_code not in ok:
        raise ApiError(r.status_code, r.text, str(r.url))
