"""A small helper to connect to Redis.

Redis is where we keep the running budget total and the per-minute request
counts, so this is the one place that knows how to open a Redis connection.
"""

import logging
from typing import Optional

import redis

logger = logging.getLogger(__name__)


class CacheClient:
    """Opens a connection to Redis."""
    
    _cached_client = None

    @staticmethod
    def get_client(
        host: str = "localhost",
        port: int = 6379,
        password: Optional[str] = None,
    ) -> "redis.Redis":
        """Connect to Redis and return the client.

        ``decode_responses=True`` makes Redis give us back normal strings
        instead of raw bytes. If Redis is not running, ``ping()`` raises a
        ``redis.ConnectionError`` -- we let it raise so the problem is obvious
        right away instead of failing in a confusing way later.
        """
        if CacheClient._cached_client is not None:
            return CacheClient._cached_client

        client = redis.Redis(
            host=host,
            port=port,
            password=password,
            decode_responses=True,
        )
        try:
            client.ping()  # check the connection works before we use it
        except redis.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to Redis at {host}:{port}. "
                "Start Redis first: `redis-server`  (or `brew services start redis` on Mac)."
            )
        logger.info("Connected to Redis at %s:%s", host, port)
        
        CacheClient._cached_client = client
        return client
