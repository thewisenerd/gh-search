import hashlib
import json
import time
import types
import typing
from dataclasses import dataclass, asdict
from pathlib import Path

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

@dataclass
class CacheKey:
    id: str

    def path(self, cache: "Cache") -> Path:
        return cache.path / f"{self.id}.json"

@dataclass
class CacheEntry:
    key: typing.Any
    value: str
    timestamp: float

    def expired(self, ttl: int) -> bool:
        return (time.time() - self.timestamp) > ttl

    def encode(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def decode(data: str) -> "CacheEntry":
        return CacheEntry(**json.loads(data))

class Cache:
    path: Path
    ttl: int

    def __init__(self, path: Path, ttl: int) -> None:
        self.path = path
        if not self.path.exists():
            self.path.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        self.expired = set()

    @staticmethod
    def hash(key: typing.Any) -> str:
        enc = json.dumps(key, sort_keys=True).encode()
        return hashlib.sha1(enc).hexdigest().lower()

    def cache_key(self, key: typing.Any) -> tuple[CacheKey, Path]:
        hash_key = Cache.hash(key)
        cache_key = CacheKey(
            id=hash_key,
        )
        return cache_key, cache_key.path(self)

    def get(self, key: typing.Any) -> str | None:
        cache_key, path = self.cache_key(key)
        if path.exists():
            logger.debug("cache hit", key=cache_key.id)
            entry = CacheEntry.decode(path.read_text())
            if entry.expired(self.ttl):
                logger.debug("cache entry expired", key=cache_key.id)
                path.unlink(missing_ok=True)
                return None
            return entry.value
        logger.debug("cache miss", key=cache_key.id)
        return None

    def put(self, key: typing.Any, value: str) -> None:
        cache_key, path = self.cache_key(key)
        logger.debug("cache put", key=cache_key.id)
        entry = CacheEntry(key, value, time.time())
        path.write_text(entry.encode())

    def __enter__(self):
        return self

    def __exit__(self, exc_type: BaseException | None, exc_val: BaseException | None, exc_tb: types.TracebackType | None) -> None:
        # todo: cleanup all expired entries on exit
        pass
