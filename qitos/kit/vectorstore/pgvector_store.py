"""PostgreSQL + pgvector vector store implementation."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from .base import VectorMatch, VectorStore


class PgVectorStore(VectorStore):
    """Vector store backed by PostgreSQL with the pgvector extension.

    Parameters
    ----------
    connection_string : str
        PostgreSQL connection string, e.g. ``"postgresql://user:pass@localhost/db"``.
    table_name : str
        Table name for storing vectors (default: ``"qitos_vectors"``).
    dimension : int
        Vector dimensionality. Used for table creation.
    embedder : callable | None
        Optional embedder for auto-embedding text on upsert.
        If provided, ``upsert`` can accept ``text`` without a pre-computed vector.
    """

    def __init__(
        self,
        connection_string: str,
        table_name: str = "qitos_vectors",
        dimension: int = 1536,
        embedder: Optional[Any] = None,
    ):
        self._connection_string = connection_string
        self._table_name = table_name
        self._dimension = dimension
        self._embedder = embedder
        self._pool = None

    def _get_pool(self):
        if self._pool is None:
            try:
                import asyncpg
            except ImportError:
                raise ImportError(
                    "asyncpg package is required for PgVectorStore. "
                    "Install with: pip install asyncpg"
                )
            # asyncpg pool creation is async; we use a sync wrapper
            # For sync usage, we create a simple connection
            self._pool = _SyncPgPool(self._connection_string)
        return self._pool

    def _ensure_table(self):
        pool = self._get_pool()
        pool.execute(f"""
            CREATE TABLE IF NOT EXISTS {self._table_name} (
                id TEXT PRIMARY KEY,
                vector vector({self._dimension}),
                metadata JSONB DEFAULT '{{}}'::jsonb,
                text TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Ensure the vector extension exists
        try:
            pool.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            pass  # Extension may already exist or require superuser

    def upsert(
        self,
        id: str,
        vector: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        text: Optional[str] = None,
    ) -> None:
        if vector is None and self._embedder is not None and text is not None:
            vector = self._embedder(text)
        if vector is None:
            raise ValueError("Either vector or text (with embedder) must be provided")

        self._ensure_table()
        pool = self._get_pool()
        vec_str = "[" + ",".join(str(v) for v in vector) + "]"
        meta_str = json.dumps(metadata or {})
        pool.execute(
            f"""
            INSERT INTO {self._table_name} (id, vector, metadata, text)
            VALUES ($1, $2::vector, $3::jsonb, $4)
            ON CONFLICT (id) DO UPDATE SET
                vector = EXCLUDED.vector,
                metadata = EXCLUDED.metadata,
                text = EXCLUDED.text
            """,
            id,
            vec_str,
            meta_str,
            text,
        )

    def query(
        self,
        vector: List[float],
        top_k: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[VectorMatch]:
        self._ensure_table()
        pool = self._get_pool()
        vec_str = "[" + ",".join(str(v) for v in vector) + "]"

        where_clause = ""
        params: list = [vec_str, top_k]
        if filter:
            conditions = []
            for i, (key, value) in enumerate(filter.items()):
                conditions.append(f"metadata->>${len(params)+1} = ${len(params)+1}")
                params.append(key)
                params.append(str(value) if not isinstance(value, str) else value)
            where_clause = "WHERE " + " AND ".join(conditions)

        rows = pool.fetchall(
            f"""
            SELECT id, 1 - (vector <=> $1::vector) AS score, metadata, text
            FROM {self._table_name}
            {where_clause}
            ORDER BY vector <=> $1::vector
            LIMIT $2
            """,
            *params,
        )

        results: List[VectorMatch] = []
        for row in rows:
            meta = row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}")
            results.append(
                VectorMatch(
                    id=row[0],
                    score=float(row[1]),
                    metadata=meta,
                    text=row[3],
                )
            )
        return results

    def delete(self, ids: List[str]) -> None:
        if not ids:
            return
        self._ensure_table()
        pool = self._get_pool()
        placeholders = ",".join(f"${i+1}" for i in range(len(ids)))
        pool.execute(
            f"DELETE FROM {self._table_name} WHERE id IN ({placeholders})",
            *ids,
        )

    def count(self) -> int:
        self._ensure_table()
        pool = self._get_pool()
        rows = pool.fetchall(f"SELECT COUNT(*) FROM {self._table_name}")
        return int(rows[0][0]) if rows else 0

    def get(self, id: str) -> Optional[VectorMatch]:
        self._ensure_table()
        pool = self._get_pool()
        rows = pool.fetchall(
            f"SELECT id, metadata, text FROM {self._table_name} WHERE id = $1",
            id,
        )
        if not rows:
            return None
        meta = rows[0][1] if isinstance(rows[0][1], dict) else json.loads(rows[0][1] or "{}")
        return VectorMatch(
            id=rows[0][0],
            score=1.0,
            metadata=meta,
            text=rows[0][2],
        )


class _SyncPgPool:
    """Synchronous wrapper around asyncpg for simple query execution."""

    def __init__(self, connection_string: str):
        self._connection_string = connection_string
        self._conn = None
        import psycopg2  # lazy import
        self._module = psycopg2

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = self._module.connect(self._connection_string)
            self._conn.autocommit = True
        return self._conn

    def execute(self, query: str, *args):
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(query, args)
            return cur

    def fetchall(self, query: str, *args):
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(query, args)
            return cur.fetchall()


__all__ = ["PgVectorStore"]
