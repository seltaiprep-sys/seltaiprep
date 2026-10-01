"""Redis-based cache manager with safe fallback and health monitoring"""

import json
import logging
import threading
import time
from typing import Any, Optional, Dict

from .config import settings

logger = logging.getLogger(__name__)

# =====================================================
# OPTIONAL REDIS IMPORT
# =====================================================
try:
    import redis
    from redis.exceptions import RedisError, ConnectionError, TimeoutError
    REDIS_OK = True
except ImportError:
    REDIS_OK = False


# =====================================================
# DATABASE MANAGER
# =====================================================
class DatabaseManager:
    """Redis-based cache manager with circuit breaker pattern"""

    def __init__(self):
        self.redis: Optional[redis.Redis] = None
        self._lock = threading.Lock()
        self._failed_attempts = 0
        self._max_failed_attempts = 3
        self._circuit_open = False
        self._last_failure_time = 0.0
        self._circuit_reset_timeout = 60 # seconds

        if not settings.enable_cache:
            logger.info("Cache disabled via settings")
            return
        if not REDIS_OK:
            logger.debug("Redis not installed - caching disabled")
            return

        self._connect()

    # =====================================================
    # CONNECTION MANAGEMENT
    # =====================================================
    def _connect(self) -> None:
        """Establish Redis connection"""
        try:
            self.redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                socket_keepalive=True,
                retry_on_timeout=True,
                health_check_interval=30,
                max_connections=20,
            )
            self.redis.ping()
            self._failed_attempts = 0
            self._circuit_open = False
            self._last_failure_time = 0.0
            logger.info(" Redis cache connected successfully")
        except (RedisError, ConnectionError, TimeoutError) as e:
            self._handle_connection_failure(e)
        except Exception as e:
            logger.error(f"Unexpected error during Redis connection: {e}")
            self._handle_connection_failure(e)

    def _handle_connection_failure(self, error: Exception) -> None:
        """Handle connection failures"""
        self._failed_attempts += 1
        self._last_failure_time = time.time()
        self.redis = None

        logger.debug(
            f"Redis connection failed (attempt {self._failed_attempts}/{self._max_failed_attempts}): {error}"
        )

        if self._failed_attempts >= self._max_failed_attempts:
            self._circuit_open = True
            logger.debug(" Circuit breaker OPEN - caching disabled temporarily")

    def _ensure_connection(self) -> bool:
        """Ensure connection with automatic circuit breaker recovery"""
        if self._circuit_open:
            if time.time() - self._last_failure_time > self._circuit_reset_timeout:
                logger.info("Circuit breaker half-open → attempting reconnection")
                self._circuit_open = False
                self._failed_attempts = 0
                self._connect()
                return self.redis is not None
            return False

        if self.redis is not None:
            return True

        self._connect()
        return self.redis is not None

    # =====================================================
    # CACHE OPERATIONS
    # =====================================================
    def cache_get(self, key: str) -> Optional[Any]:
        """Get cached value safely"""
        if not self._ensure_connection():
            return None
        try:
            data = self.redis.get(key)
            if not data:
                return None
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.debug(f"Corrupted cache entry deleted: {key[:80]}")
            self.cache_delete(key)
            return None
        except (RedisError, ConnectionError, TimeoutError) as e:
            logger.debug(f"Redis GET failed for '{key[:80]}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in cache_get('{key[:80]}'): {e}")
            return None

    def cache_set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set cache with TTL"""
        if not self._ensure_connection():
            return
        ttl = ttl or settings.cache_ttl
        try:
            serialized = json.dumps(
                value, default=self._json_serializer, ensure_ascii=False
            )
            with self._lock:
                self.redis.setex(key, ttl, serialized)
        except TypeError as e:
            logger.debug(f"Serialization error for '{key[:80]}': {e}")
        except (RedisError, ConnectionError, TimeoutError) as e:
            logger.debug(f"Redis SET failed for '{key[:80]}': {e}")
        except Exception as e:
            logger.error(f"Unexpected error in cache_set('{key[:80]}'): {e}")

    def cache_delete(self, key: str) -> None:
        if not self._ensure_connection():
            return
        try:
            self.redis.delete(key)
        except Exception as e:
            logger.debug(f"Cache DELETE failed for '{key[:80]}': {e}")

    def cache_delete_pattern(self, pattern: str) -> int:
        """Safe pattern deletion using SCAN"""
        if not self._ensure_connection():
            return 0
        try:
            cursor = 0
            deleted = 0
            while True:
                cursor, keys = self.redis.scan(
                    cursor=cursor, match=pattern, count=1000
                )
                if keys:
                    deleted += self.redis.delete(*keys)
                if cursor == 0:
                    break
            if deleted > 0:
                logger.debug(f"Deleted {deleted} keys matching '{pattern}'")
            return deleted
        except Exception as e:
            logger.debug(f"Pattern delete '{pattern}' failed: {e}")
            return 0

    # =====================================================
    # BULK OPERATIONS
    # =====================================================
    def cache_get_many(self, keys: list[str]) -> Dict[str, Any]:
        if not self._ensure_connection() or not keys:
            return {}
        try:
            values = self.redis.mget(keys)
            result: Dict[str, Any] = {}
            for key, value in zip(keys, values):
                if value:
                    try:
                        result[key] = json.loads(value)
                    except json.JSONDecodeError:
                        logger.debug(f"Corrupted cache entry: {key}")
                        result[key] = None
                        self.cache_delete(key) # Clean up
                else:
                    result[key] = None
            return result
        except Exception as e:
            logger.debug(f"Cache MGET failed: {e}")
            return {}

    def cache_set_many(self, mapping: Dict[str, Any], ttl: Optional[int] = None) -> None:
        if not self._ensure_connection() or not mapping:
            return
        ttl = ttl or settings.cache_ttl
        try:
            with self._lock:
                pipe = self.redis.pipeline()
                for key, value in mapping.items():
                    serialized = json.dumps(
                        value, default=self._json_serializer, ensure_ascii=False
                    )
                    pipe.setex(key, ttl, serialized)
                pipe.execute()
        except Exception as e:
            logger.debug(f"Cache MSET failed: {e}")

    # =====================================================
    # UTILITY METHODS
    # =====================================================
    def cache_exists(self, key: str) -> bool:
        if not self._ensure_connection():
            return False
        try:
            return bool(self.redis.exists(key))
        except Exception:
            return False

    def cache_ttl(self, key: str) -> int:
        if not self._ensure_connection():
            return -2
        try:
            return self.redis.ttl(key)
        except Exception:
            return -2

    def cache_incr(self, key: str, amount: int = 1) -> Optional[int]:
        if not self._ensure_connection():
            return None
        try:
            return self.redis.incrby(key, amount)
        except Exception as e:
            logger.debug(f"Cache INCR failed for '{key}': {e}")
            return None

    # =====================================================
    # HEALTH & STATS
    # =====================================================
    def is_healthy(self) -> bool:
        if self._circuit_open or not self.redis:
            return False
        try:
            return bool(self.redis.ping())
        except Exception:
            return False

    def get_stats(self) -> dict:
        return {
            "enabled": settings.enable_cache,
            "installed": REDIS_OK,
            "connected": self.redis is not None,
            "healthy": self.is_healthy(),
            "circuit_open": self._circuit_open,
            "failed_attempts": self._failed_attempts,
            "circuit_reset_timeout": self._circuit_reset_timeout,
        }

    # =====================================================
    # HELPERS
    # =====================================================
    @staticmethod
    def _json_serializer(obj: Any) -> str:
        if hasattr(obj, 'tolist'): # numpy arrays
            return str(obj.tolist())
        if hasattr(obj, 'isoformat'): # datetime objects
            return obj.isoformat()
        if hasattr(obj, '__dict__'):
            return str(obj.__dict__)
        return str(obj)

    def reset_circuit(self) -> None:
        self._circuit_open = False
        self._failed_attempts = 0
        self._last_failure_time = 0.0
        self._connect()

    def close(self) -> None:
        if self.redis:
            try:
                self.redis.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.debug(f"Error closing Redis: {e}")
            self.redis = None


# =====================================================
# SINGLETON
# =====================================================
db_manager = DatabaseManager()