"""Cached, rate-limited HTTP for public biology APIs.

Every response is written to data/seeds/<source_id>/<hash>.json alongside the
request that produced it. Two reasons this matters beyond speed:

1. Reproducibility. The generated graph claims a `source_url` and
   `source_retrieved_at` on every seed-derived node. Those claims are only
   truthful if the raw response is still on disk to check them against.
2. Courtesy. These are free public endpoints, several of them (NCBI, EBI)
   with published rate limits. The cache means re-running generation costs
   the upstream services nothing.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

SEED_ROOT = Path(__file__).resolve().parents[3] / "data" / "seeds"

# Politeness limits. NCBI allows 3 req/s without an API key; EBI and Ensembl
# publish ~10-15 req/s. We stay under all of them.
RATE_LIMITS_RPS: dict[str, float] = {
    "ncbi": 2.5,
    "ensembl": 10.0,
    "uniprot": 8.0,
    "ebi": 8.0,
    "reactome": 8.0,
    "opentargets": 5.0,
    "chembl": 5.0,
    "string": 2.0,
    "hgnc": 8.0,
    "hpo": 8.0,
    "gtex": 4.0,
    "default": 5.0,
}

_USER_AGENT = "skygenic-scans/0.1 (graph structure validation; contact ramzanm461@gmail.com)"


class _RateLimiter:
    """Per-host token spacing. Threadsafe so fetchers can be parallelised later."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str) -> None:
        rps = RATE_LIMITS_RPS.get(key, RATE_LIMITS_RPS["default"])
        min_gap = 1.0 / rps
        with self._lock:
            now = time.monotonic()
            prev = self._last.get(key, 0.0)
            sleep_for = min_gap - (now - prev)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last[key] = time.monotonic()


_limiter = _RateLimiter()


@dataclass
class FetchResult:
    """A response plus the provenance needed to defend it later."""

    data: Any
    url: str
    source_id: str
    retrieved_at: datetime
    from_cache: bool
    cache_path: Path


@dataclass
class Fetcher:
    source_id: str
    rate_key: str = "default"
    timeout: float = 30.0
    _stats: dict[str, int] = field(default_factory=lambda: {"hit": 0, "miss": 0, "error": 0})

    @property
    def cache_dir(self) -> Path:
        d = SEED_ROOT / self.source_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _cache_path(self, url: str, body: Any = None) -> Path:
        key = url if body is None else f"{url}|{json.dumps(body, sort_keys=True)}"
        return self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        _limiter.wait(self.rate_key)
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT, **kw.pop("headers", {})}
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as c:
            r = c.request(method, url, headers=headers, **kw)
        # 404 is a legitimate "no such record" for these APIs; don't burn retries on it.
        if r.status_code == 404:
            return r
        r.raise_for_status()
        return r

    def get(self, url: str, *, params: dict | None = None,
            parse_json: bool = True) -> FetchResult | None:
        full = url if not params else str(httpx.URL(url, params=params))
        cp = self._cache_path(full)
        if cp.exists():
            self._stats["hit"] += 1
            payload = json.loads(cp.read_text())
            return FetchResult(payload["data"], full, self.source_id,
                               datetime.fromisoformat(payload["retrieved_at"]), True, cp)
        try:
            r = self._request("GET", url, params=params)
        except Exception:
            self._stats["error"] += 1
            return None
        if r.status_code == 404:
            self._stats["error"] += 1
            return None
        self._stats["miss"] += 1
        data = r.json() if parse_json else r.text
        ts = datetime.now(timezone.utc)
        cp.write_text(json.dumps({"url": full, "retrieved_at": ts.isoformat(), "data": data}))
        return FetchResult(data, full, self.source_id, ts, False, cp)

    def post_json(self, url: str, body: dict) -> FetchResult | None:
        cp = self._cache_path(url, body)
        if cp.exists():
            self._stats["hit"] += 1
            payload = json.loads(cp.read_text())
            return FetchResult(payload["data"], url, self.source_id,
                               datetime.fromisoformat(payload["retrieved_at"]), True, cp)
        try:
            r = self._request("POST", url, json=body)
        except Exception:
            self._stats["error"] += 1
            return None
        self._stats["miss"] += 1
        data = r.json()
        ts = datetime.now(timezone.utc)
        cp.write_text(json.dumps({"url": url, "retrieved_at": ts.isoformat(),
                                  "request": body, "data": data}))
        return FetchResult(data, url, self.source_id, ts, False, cp)

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)
