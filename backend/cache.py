import os
import sqlite3
import json
import hashlib
import time
from abc import ABC, abstractmethod

class BaseCacheManager(ABC):
    """Abstract base class defining the cache contract for the NL2SQL Agent."""

    @abstractmethod
    def get(self, question: str) -> dict | None:
        """Retrieve cached query details for a question if they exist and are not expired."""
        pass

    @abstractmethod
    def set(self, question: str, query: str, result: any, summary: str, tokens: dict | None) -> None:
        """Store query details, database results, conversational summaries, and token usage in the cache."""
        pass

    @abstractmethod
    def get_sql(self, sql: str) -> list | None:
        """Retrieve cached SQL execution results if they exist and are not expired."""
        pass

    @abstractmethod
    def set_sql(self, sql: str, result: list) -> None:
        """Store SQL execution results in the cache."""
        pass

    @abstractmethod
    def delete(self, key_hash: str) -> None:
        """Remove a specific cache entry by its key hash."""
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear all entries in the cache."""
        pass


class SQLiteCacheManager(BaseCacheManager):
    """A lightweight SQLite cache manager to persist natural language questions,
    their compiled SQL queries, database results, conversational summaries, and token metrics.
    """

    def __init__(self, cache_db_path: str = None, ttl_seconds: int = 3600) -> None:
        if cache_db_path is None:
            BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            cache_db_path = os.path.join(BASE_DIR, "cache.db")
        
        self.db_path = cache_db_path
        self.ttl = ttl_seconds
        self._init_db()

    def _init_db(self) -> None:
        """Initializes the cache table if it does not exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_cache (
                    key_hash TEXT PRIMARY KEY,
                    question TEXT,
                    query TEXT,
                    result TEXT,
                    summary TEXT,
                    tokens TEXT,
                    created_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sql_cache (
                    key_hash TEXT PRIMARY KEY,
                    query TEXT,
                    result TEXT,
                    created_at REAL
                )
            """)
            conn.commit()

    def get(self, question: str) -> dict | None:
        """Gets cached details for a question if they exist and are not expired."""
        if self.ttl <= 0:
            return None  # Caching is disabled

        key_hash = hashlib.sha256(question.lower().strip().encode("utf-8")).hexdigest()
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT query, result, summary, tokens, created_at FROM query_cache WHERE key_hash = ?",
                    (key_hash,)
                )
                row = cursor.fetchone()
                
                if row:
                    query, result_json, summary, tokens_json, created_at = row
                    age = time.time() - created_at
                    
                    if age <= self.ttl:
                        print(f"\n[Cache Hit] Serving result from SQLite cache (age: {int(age)}s, TTL: {self.ttl}s)")
                        return {
                            "query": query,
                            "result": json.loads(result_json) if result_json else [],
                            "summary": summary or "",
                            "tokens": json.loads(tokens_json) if tokens_json else None
                        }
                    else:
                        print(f"\n[Cache Expired] Cache row found but expired (age: {int(age)}s, TTL: {self.ttl}s). Purging...")
                        self.delete(key_hash)
        except Exception as e:
            print(f"Error reading from SQLite cache: {e}")
            
        return None

    def set(self, question: str, query: str, result: any, summary: str, tokens: dict | None) -> None:
        """Stores query details and results in the cache."""
        if self.ttl <= 0:
            return  # Caching is disabled

        key_hash = hashlib.sha256(question.lower().strip().encode("utf-8")).hexdigest()
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO query_cache (key_hash, question, query, result, summary, tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        key_hash,
                        question.strip(),
                        query,
                        json.dumps(result),
                        summary,
                        json.dumps(tokens) if tokens else None,
                        time.time()
                    )
                )
                conn.commit()
                print(f"[Cache Store] Persisted query details to SQLite cache (key: {key_hash[:10]}...)")
        except Exception as e:
            print(f"Error writing to SQLite cache: {e}")

    def get_sql(self, sql: str) -> list | None:
        """Gets cached database results for a physical SQL query if they exist and are not expired."""
        if self.ttl <= 0:
            return None

        key_hash = hashlib.sha256(sql.lower().strip().encode("utf-8")).hexdigest()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT result, created_at FROM sql_cache WHERE key_hash = ?",
                    (key_hash,)
                )
                row = cursor.fetchone()
                if row:
                    result_json, created_at = row
                    age = time.time() - created_at
                    if age <= self.ttl:
                        print(f"\n[Federated Cache Hit] Serving remote sub-query result from SQLite cache (age: {int(age)}s)")
                        return json.loads(result_json) if result_json else []
                    else:
                        print(f"\n[Federated Cache Expired] SQL cache row expired (age: {int(age)}s). Purging...")
                        conn.execute("DELETE FROM sql_cache WHERE key_hash = ?", (key_hash,))
                        conn.commit()
        except Exception as e:
            print(f"Error reading SQL cache: {e}")
        return None

    def set_sql(self, sql: str, result: list) -> None:
        """Stores physical SQL query results in the cache."""
        if self.ttl <= 0:
            return

        key_hash = hashlib.sha256(sql.lower().strip().encode("utf-8")).hexdigest()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO sql_cache (key_hash, query, result, created_at) VALUES (?, ?, ?, ?)",
                    (
                        key_hash,
                        sql.strip(),
                        json.dumps(result),
                        time.time()
                    )
                )
                conn.commit()
                print(f"[Federated Cache Store] Persisted sub-query result to SQLite cache (key: {key_hash[:10]}...)")
        except Exception as e:
            print(f"Error writing SQL cache: {e}")

    def delete(self, key_hash: str) -> None:
        """Removes a specific cache row by hash key."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM query_cache WHERE key_hash = ?", (key_hash,))
                conn.commit()
        except Exception as e:
            print(f"Error deleting cache row: {e}")

    def clear(self) -> None:
        """Clears all cached queries from the SQLite cache tables."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM query_cache")
                conn.execute("DELETE FROM sql_cache")
                conn.commit()
                print("[Cache Clear] SQLite cache cleared.")
        except Exception as e:
            print(f"Error clearing SQLite cache: {e}")


class RedisCacheManager(BaseCacheManager):
    """A Redis-backed cache manager for distributed production-grade caching."""

    def __init__(self, redis_url: str, ttl_seconds: int = 3600) -> None:
        try:
            import redis
        except ImportError as e:
            raise ImportError(
                "The 'redis' library is required to use RedisCacheManager. "
                "Install it using `pip install redis`."
            ) from e
        
        self.ttl = ttl_seconds
        # Connect to Redis
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def ping(self) -> bool:
        """Ping Redis server to check connection health."""
        return self.client.ping()

    def _get_key(self, question: str) -> str:
        """Generates a unique Redis key using SHA256 hash of the normalized question."""
        key_hash = hashlib.sha256(question.lower().strip().encode("utf-8")).hexdigest()
        return f"nl2sql:cache:{key_hash}"

    def get(self, question: str) -> dict | None:
        """Gets cached details for a question if they exist."""
        if self.ttl <= 0:
            return None

        key = self._get_key(question)
        try:
            cached_data = self.client.get(key)
            if cached_data:
                print("\n[Cache Hit] Serving result from Redis cache")
                return json.loads(cached_data)
        except Exception as e:
            print(f"Error reading from Redis cache: {e}")
        return None

    def set(self, question: str, query: str, result: any, summary: str, tokens: dict | None) -> None:
        """Stores query details and results in Redis with TTL expiration."""
        if self.ttl <= 0:
            return

        key = self._get_key(question)
        payload = {
            "query": query,
            "result": result,
            "summary": summary,
            "tokens": tokens
        }
        
        try:
            self.client.setex(
                name=key,
                time=self.ttl,
                value=json.dumps(payload)
            )
            print(f"[Cache Store] Persisted query details to Redis cache (key: {key})")
        except Exception as e:
            print(f"Error writing to Redis cache: {e}")

    def _get_sql_key(self, sql: str) -> str:
        """Generates a unique Redis key using SHA256 hash of the normalized SQL query."""
        key_hash = hashlib.sha256(sql.lower().strip().encode("utf-8")).hexdigest()
        return f"nl2sql:sql_cache:{key_hash}"

    def get_sql(self, sql: str) -> list | None:
        """Retrieve cached SQL query results from Redis."""
        if self.ttl <= 0:
            return None

        key = self._get_sql_key(sql)
        try:
            cached_data = self.client.get(key)
            if cached_data:
                print("\n[Federated Cache Hit] Serving remote sub-query result from Redis cache")
                return json.loads(cached_data)
        except Exception as e:
            print(f"Error reading SQL cache from Redis: {e}")
        return None

    def set_sql(self, sql: str, result: list) -> None:
        """Store SQL query results in Redis cache with TTL."""
        if self.ttl <= 0:
            return

        key = self._get_sql_key(sql)
        try:
            self.client.setex(
                name=key,
                time=self.ttl,
                value=json.dumps(result)
            )
            print(f"[Federated Cache Store] Persisted sub-query result to Redis cache (key: {key})")
        except Exception as e:
            print(f"Error writing SQL cache to Redis: {e}")

    def delete(self, key_hash: str) -> None:
        """Removes a specific cache key using its raw hash."""
        key = f"nl2sql:cache:{key_hash}"
        try:
            self.client.delete(key)
        except Exception as e:
            print(f"Error deleting key from Redis cache: {e}")

    def clear(self) -> None:
        """Clears all cached queries from Redis matching the namespace."""
        try:
            keys = self.client.keys("nl2sql:cache:*")
            sql_keys = self.client.keys("nl2sql:sql_cache:*")
            all_keys = keys + sql_keys
            if all_keys:
                self.client.delete(*all_keys)
                print(f"[Cache Clear] Cleared {len(all_keys)} keys from Redis.")
        except Exception as e:
            print(f"Error clearing Redis cache: {e}")


def get_cache_manager(ttl_seconds: int = 3600) -> BaseCacheManager:
    """Dynamic Cache Factory.
    Decides between Redis and SQLite based on environment configuration.
    """
    redis_url = os.environ.get("REDIS_URL")
    
    if redis_url:
        try:
            # Mask URL password for clean console logging
            masked_url = redis_url
            if "@" in redis_url:
                parts = redis_url.split("@")
                masked_url = "redis://****@" + parts[-1]
            print(f"[Cache Router] REDIS_URL discovered: {masked_url}. Attempting connection...")
            cache = RedisCacheManager(redis_url=redis_url, ttl_seconds=ttl_seconds)
            if cache.ping():
                print("[Cache Router] Redis connection successful! Booting Redis cache...")
                return cache
        except Exception as e:
            print(f"[Cache Router] Redis URL was provided but connection failed: {e}. Cascading to SQLite...")
            
    # Fallback to local SQLite cache
    print("[Cache Router] No Redis configuration or connection. Cascading to local SQLite...")
    return SQLiteCacheManager(ttl_seconds=ttl_seconds)
