from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, Tuple

from fastapi import HTTPException, status

from .config import Settings

try:
    import redis.asyncio as redis
except ImportError:  # pragma: no cover
    redis = None


class InMemoryTokenBucket:
    def __init__(self, capacity: int, refill_per_second: float):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens: float = capacity
        self.updated_at = time.time()
        self.lock = asyncio.Lock()

    async def consume(self, amount: float = 1.0) -> bool:
        async with self.lock:
            now = time.time()
            delta = now - self.updated_at
            self.tokens = min(self.capacity, self.tokens + delta * self.refill_per_second)
            self.updated_at = now
            if self.tokens >= amount:
                self.tokens -= amount
                return True
            return False


class RateLimiter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._buckets: Dict[str, InMemoryTokenBucket] = {}
        self._daily_usage: Dict[str, Tuple[str, int]] = {}
        self._lock = asyncio.Lock()
        self._redis = None
        if settings.redis_url and redis is not None:
            self._redis = redis.from_url(settings.redis_url, decode_responses=True)

    async def check(self, product_id: str, user_id: str) -> None:
        if self._redis:
            await self._check_with_redis(product_id, user_id)
        else:
            await self._check_in_memory(product_id, user_id)

    async def _check_in_memory(self, product_id: str, user_id: str) -> None:
        key = f"{product_id}:{user_id}"
        async with self._lock:
            if key not in self._buckets:
                self._buckets[key] = InMemoryTokenBucket(
                    capacity=self.settings.rate_limit.bucket_capacity,
                    refill_per_second=self.settings.rate_limit.bucket_refill_per_second,
                )
            bucket = self._buckets[key]

        if not await bucket.consume():
            # 次回リトライまでの待機時間を計算
            wait_seconds = max(1, int((1.0 - bucket.tokens) / self.settings.rate_limit.bucket_refill_per_second) + 1)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {wait_seconds} seconds"
            )

        async with self._lock:
            # UTC基準で日付を取得（Cloud Runのデフォルトタイムゾーン）
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            stored_day, used = self._daily_usage.get(key, (today, 0))
            if stored_day != today:
                used = 0
                stored_day = today
            if used >= self.settings.rate_limit.daily_quota:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Daily quota exceeded ({self.settings.rate_limit.daily_quota} requests/day)"
                )
            self._daily_usage[key] = (stored_day, used + 1)

    async def _check_with_redis(self, product_id: str, user_id: str) -> None:
        assert self._redis is not None
        bucket_key = f"{self.settings.redis_prefix}:bucket:{product_id}:{user_id}"
        # UTC基準で日付を取得（Cloud Runのデフォルトタイムゾーン）
        today_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        quota_key = f"{self.settings.redis_prefix}:quota:{product_id}:{user_id}:{today_utc}"

        script = """
        local bucket_key = KEYS[1]
        local quota_key = KEYS[2]
        local capacity = tonumber(ARGV[1])
        local refill = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])
        local bucket_ttl = tonumber(ARGV[4])
        local quota_ttl = tonumber(ARGV[5])
        local daily_quota = tonumber(ARGV[6])

        local bucket = redis.call('HMGET', bucket_key, 'tokens', 'updated_at')
        local tokens = tonumber(bucket[1]) or capacity
        local updated_at = tonumber(bucket[2]) or now
        local elapsed = now - updated_at
        tokens = math.min(capacity, tokens + elapsed * refill)

        local allowed = 0
        local used = tonumber(redis.call('GET', quota_key) or '0')
        if used < daily_quota and tokens >= 1 then
            tokens = tokens - 1
            allowed = 1
            used = tonumber(redis.call('INCR', quota_key))
            if used == 1 then
                redis.call('EXPIRE', quota_key, quota_ttl)
            end
            if used > daily_quota then
                allowed = 0
            end
        end

        redis.call('HMSET', bucket_key, 'tokens', tokens, 'updated_at', now)
        redis.call('EXPIRE', bucket_key, bucket_ttl)

        return {allowed, tokens, used, daily_quota}
        """

        now = time.time()
        bucket_ttl = max(int(self.settings.rate_limit.bucket_capacity / max(self.settings.rate_limit.bucket_refill_per_second, 0.001) * 2), 60)
        quota_ttl = 24 * 60 * 60
        allowed, _tokens, used, daily_quota = await self._redis.eval(
            script,
            numkeys=2,
            keys=[bucket_key, quota_key],
            args=[
                self.settings.rate_limit.bucket_capacity,
                self.settings.rate_limit.bucket_refill_per_second,
                now,
                bucket_ttl,
                quota_ttl,
                self.settings.rate_limit.daily_quota,
            ],
        )

        if used > daily_quota:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily quota exceeded ({daily_quota} requests/day)"
            )

        if allowed != 1:
            # Redisモードでは正確な待機時間計算が困難なため、概算値を返す
            wait_seconds = max(1, int(1.0 / self.settings.rate_limit.bucket_refill_per_second) + 1)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {wait_seconds} seconds"
            )
