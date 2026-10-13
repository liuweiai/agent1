from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Any

from redis import Redis
from redis.client import PubSub

from .config import Settings


RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class RunLease:
    key: str
    owner: str


class RedisEventSubscription:
    def __init__(self, pubsub: PubSub):
        self._pubsub = pubsub

    def wait(self, timeout_seconds: float = 0.5) -> bool:
        message = self._pubsub.get_message(
            ignore_subscribe_messages=True, timeout=timeout_seconds
        )
        return bool(message)

    def close(self) -> None:
        self._pubsub.close()


class RedisCoordinator:
    """Redis-backed cross-process locking and low-latency run notifications.

    MySQL remains the durable source of truth. Redis only coordinates workers and
    wakes SSE subscribers; losing Redis never invents business state.
    """

    def __init__(self, settings: Settings):
        self.enabled = settings.redis_enabled
        self.fail_open = settings.redis_fail_open
        self.key_prefix = settings.redis_key_prefix.rstrip(":")
        self.lock_ttl_seconds = settings.redis_lock_ttl_seconds
        self._client: Redis | None = None
        if self.enabled:
            try:
                self._client = Redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=3,
                    health_check_interval=30,
                )
                self._client.ping()
            except Exception:
                self._client = None
                if not self.fail_open:
                    raise

    @property
    def available(self) -> bool:
        return self._client is not None

    def ping(self) -> bool:
        if not self._client:
            return False
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def acquire_run_lock(self, run_id: str) -> RunLease | None:
        if not self._client:
            return None
        owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
        key = f"{self.key_prefix}:lock:run:{run_id}"
        acquired = self._client.set(key, owner, nx=True, ex=self.lock_ttl_seconds)
        return RunLease(key=key, owner=owner) if acquired else None

    def release_run_lock(self, lease: RunLease | None) -> None:
        if not self._client or not lease:
            return
        self._client.eval(RELEASE_LOCK_SCRIPT, 1, lease.key, lease.owner)

    def publish_run_signal(self, payload: dict[str, Any]) -> None:
        if not self._client:
            return
        run_id = str(payload.get("run_id", ""))
        if not run_id:
            return
        try:
            encoded = json.dumps(payload, ensure_ascii=False, default=str)
            self._client.publish(f"{self.key_prefix}:events:{run_id}", encoded)
            self._client.setex(
                f"{self.key_prefix}:last-event:{run_id}", 3600, encoded
            )
        except Exception:
            if not self.fail_open:
                raise

    def subscribe(self, run_id: str) -> RedisEventSubscription | None:
        if not self._client:
            return None
        try:
            pubsub = self._client.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(f"{self.key_prefix}:events:{run_id}")
            return RedisEventSubscription(pubsub)
        except Exception:
            if not self.fail_open:
                raise
            return None

    def close(self) -> None:
        if self._client:
            self._client.close()
