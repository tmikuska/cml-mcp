# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.

# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.

"""Definitions for a cache for session management."""

import asyncio
import hashlib
import logging
import time
from asyncio import Lock
from dataclasses import dataclass, field
from typing import Dict, Optional

from cml_mcp.cml_client import CMLClient

logger = logging.getLogger("cml-mcp.cache")


def _redact_key(key: str) -> str:
    """Hash a cache key before logging it; cache keys embed the raw username and must never appear in logs."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass
class CacheEntry:
    """Represents a cache entry with a value and an expiration time."""

    value: CMLClient
    timestamp: float = field(default_factory=time.time)

    def is_expired(self, ttl: int) -> bool:
        """Check if cache entry has exceeded TTL."""
        return (time.time() - self.timestamp) > ttl


class ThreadSafeCache:
    """Thread-safe cache with TTL support."""

    def __init__(self, ttl: int = 3600, sweep_interval: int | None = None):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = Lock()
        self._ttl = ttl
        # How often the background sweep (see _sweep_loop) walks the whole cache
        # evicting expired entries. Defaults to the TTL itself, capped at 300s so a
        # very large CML_SESSION_TTL doesn't leave stale entries (and their open
        # httpx.AsyncClient connection pools) sitting around for hours before the
        # first sweep.
        self._sweep_interval = sweep_interval if sweep_interval is not None else min(ttl, 300)
        self._sweep_task: asyncio.Task | None = None

    def _ensure_sweeper_started(self) -> None:
        """Lazily start the background eviction sweep on first cache access.

        get() only evicts an expired entry when that same key is looked up again, so a
        client that is cached once and never looked up again (e.g. a one-off token used
        for a single burst of requests) would otherwise stay in memory -- along with its
        open httpx.AsyncClient connection pool -- for the entire life of the process.
        This periodic sweep instead walks the whole cache every _sweep_interval seconds
        and evicts anything past its TTL regardless of whether it is ever looked up
        again, bounding memory use by "recently active unique credentials" rather than
        "every credential ever seen".

        Started lazily here (not in __init__) because ThreadSafeCache is constructed at
        import time in tools/dependencies.py, before any asyncio event loop is running.
        """
        if self._sweep_task is not None and not self._sweep_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop yet (e.g. called from sync code or before the server
            # event loop starts); the next get()/set() call will retry.
            return
        self._sweep_task = loop.create_task(self._sweep_loop())

    async def _sweep_loop(self) -> None:
        """Evict expired entries every _sweep_interval seconds until cancelled."""
        try:
            while True:
                await asyncio.sleep(self._sweep_interval)
                await self._sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background cache sweep failed; sweeping will not resume")

    async def _sweep_once(self) -> None:
        """Evict every currently-expired entry in a single pass."""
        async with self._lock:
            expired_keys = [key for key, entry in self._cache.items() if entry.is_expired(self._ttl)]
            expired_clients = [self._cache.pop(key).value for key in expired_keys]
        if expired_clients:
            logger.debug(
                "Background sweep evicting %d expired cache entr%s", len(expired_clients), "y" if len(expired_clients) == 1 else "ies"
            )
            # return_exceptions=True so one client's close() failure can't propagate out of the
            # sweep loop and permanently stop future sweeps (see _sweep_loop's except clause).
            await asyncio.gather(*(client.close() for client in expired_clients), return_exceptions=True)

    async def get(self, key: str) -> Optional[CMLClient]:
        """Retrieve value from cache if not expired."""
        self._ensure_sweeper_started()
        expired_client = None
        async with self._lock:
            entry = self._cache.get(key)
            if entry and not entry.is_expired(self._ttl):
                entry.timestamp = time.time()
                return entry.value
            elif entry:
                logger.debug("Cache entry for key %s has expired", _redact_key(key))
                del self._cache[key]
                expired_client = entry.value
        if expired_client:
            await expired_client.close()
        return None

    async def set(self, key: str, value: CMLClient) -> None:
        """Store value in cache with current timestamp, closing any displaced entry."""
        self._ensure_sweeper_started()
        async with self._lock:
            old_entry = self._cache.get(key)
            self._cache[key] = CacheEntry(value=value)
        if old_entry and old_entry.value is not value:
            await old_entry.value.close()

    async def clear(self) -> None:
        """Clear all cache entries and close all sessions.

        NOTE: Use-after-close race — a concurrent request that already received a
        client reference via get() may still be mid-flight when this closes it.
        That request will encounter a 'client already closed' error, but
        CMLClient.check_authentication() will recover by re-logging in on the
        next call.  This is acceptable given how rarely clear() is invoked.
        """
        async with self._lock:
            logger.debug("Clearing entire cache")
            entries = list(self._cache.values())
            self._cache.clear()
        await asyncio.gather(*(e.value.close() for e in entries))

    async def rekey(self, old_key: str, new_key: str) -> None:
        """Move a cache entry from old_key to new_key without closing the moved client.

        Used after a cached client's credentials are rotated in place (e.g.
        CMLClient.set_jwt() via the set_cml_jwt tool), so the client becomes
        reachable only under a key matching its *new* credentials -- a request that
        still presents the stale old credentials will then miss the cache and be
        forced to authenticate from scratch, rather than transparently reusing the
        rotated session.

        If new_key already holds a *different* entry, that displaced entry is closed
        (same behavior as set()). No-ops if old_key is not present (e.g. it was
        already evicted or re-keyed concurrently) or if old_key == new_key.
        """
        if old_key == new_key:
            return
        async with self._lock:
            entry = self._cache.pop(old_key, None)
            if entry is None:
                return
            displaced = self._cache.get(new_key)
            self._cache[new_key] = entry
        if displaced and displaced.value is not entry.value:
            await displaced.value.close()

    async def invalidate(self, key: str) -> None:
        """Remove specific cache entry and close its session.

        NOTE: Use-after-close race — a concurrent request that already received
        this client via get() may still be using it when it is closed here.
        In practice invalidate() is only called after a re-auth failure, meaning
        the client was already broken, so any concurrent request using it would
        have failed regardless.  CMLClient.check_authentication() handles recovery.
        """
        async with self._lock:
            logger.debug("Invalidating cache entry for key: %s", _redact_key(key))
            entry = self._cache.pop(key, None)
        if entry:
            await entry.value.close()
