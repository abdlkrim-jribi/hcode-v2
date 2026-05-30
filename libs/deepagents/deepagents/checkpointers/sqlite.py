"""SQLite-backed LangGraph checkpoint saver for HCode v2."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

import aiosqlite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

_DDL_CHECKPOINTS = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint BLOB NOT NULL,
    metadata BLOB NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
)
"""

_DDL_BLOBS = """
CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
)
"""

_DDL_WRITES = """
CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BLOB NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
)
"""


def _pack(type_str: str, data: bytes) -> bytes:
    """Encode (type_str, bytes) into a single BLOB for the metadata column."""
    hdr = type_str.encode()
    return len(hdr).to_bytes(2, "big") + hdr + data


def _unpack(blob: bytes) -> tuple[str, bytes]:
    """Decode a BLOB produced by _pack back into (type_str, bytes)."""
    n = int.from_bytes(blob[:2], "big")
    return blob[2 : 2 + n].decode(), blob[2 + n :]


class HCodeSQLiteCheckpointer(BaseCheckpointSaver):
    """SQLite-backed LangGraph checkpoint saver.

    Stores conversation checkpoints in a local SQLite DB, enabling persistent
    history across hcode sessions. Uses aiosqlite for fully async I/O.

    Args:
        db_path: Path to the SQLite file. Parent directories are created
            automatically. Defaults to ``.hcode/sessions/default.db``.
    """

    def __init__(self, db_path: str = ".hcode/sessions/default.db") -> None:
        super().__init__()
        self.db_path = str(Path(db_path))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    async def _setup(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(_DDL_CHECKPOINTS)
        await conn.execute(_DDL_BLOBS)
        await conn.execute(_DDL_WRITES)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.commit()

    # -- internal helpers -------------------------------------------------

    async def _load_blobs(
        self,
        conn: aiosqlite.Connection,
        thread_id: str,
        checkpoint_ns: str,
        versions: dict[str, Any],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for channel, version in versions.items():
            async with conn.execute(
                "SELECT type, blob FROM checkpoint_blobs "
                "WHERE thread_id=? AND checkpoint_ns=? AND channel=? AND version=?",
                (thread_id, checkpoint_ns, channel, str(version)),
            ) as cur:
                row = await cur.fetchone()
            if row and row[0] != "empty":
                result[channel] = self.serde.loads_typed((row[0], bytes(row[1])))
        return result

    async def _load_writes(
        self,
        conn: aiosqlite.Connection,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> list[tuple[str, str, Any]]:
        async with conn.execute(
            "SELECT task_id, channel, type, blob FROM checkpoint_writes "
            "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? ORDER BY idx",
            (thread_id, checkpoint_ns, checkpoint_id),
        ) as cur:
            rows = await cur.fetchall()
        return [
            (row[0], row[1], self.serde.loads_typed((row[2], bytes(row[3]))))
            for row in rows
        ]

    async def _build_tuple(
        self,
        conn: aiosqlite.Connection,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        parent_checkpoint_id: str | None,
        cp_type: str,
        cp_blob: bytes,
        meta_blob: bytes,
        config: RunnableConfig | None = None,
    ) -> CheckpointTuple:
        checkpoint_: Checkpoint = self.serde.loads_typed((cp_type, bytes(cp_blob)))
        meta_type, meta_data = _unpack(bytes(meta_blob))
        metadata = self.serde.loads_typed((meta_type, meta_data))
        channel_values = await self._load_blobs(
            conn, thread_id, checkpoint_ns, checkpoint_["channel_versions"]
        )
        pending_writes = await self._load_writes(conn, thread_id, checkpoint_ns, checkpoint_id)
        cfg: RunnableConfig = config or {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }
        parent_config: RunnableConfig | None = (
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_checkpoint_id,
                }
            }
            if parent_checkpoint_id
            else None
        )
        return CheckpointTuple(
            config=cfg,
            checkpoint={**checkpoint_, "channel_values": channel_values},
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    # -- public API -------------------------------------------------------

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        async with aiosqlite.connect(self.db_path) as conn:
            await self._setup(conn)
            if checkpoint_id:
                async with conn.execute(
                    "SELECT checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata "
                    "FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                    (thread_id, checkpoint_ns, checkpoint_id),
                ) as cur:
                    row = await cur.fetchone()
            else:
                async with conn.execute(
                    "SELECT checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata "
                    "FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? "
                    "ORDER BY checkpoint_id DESC LIMIT 1",
                    (thread_id, checkpoint_ns),
                ) as cur:
                    row = await cur.fetchone()

            if not row:
                return None

            return await self._build_tuple(
                conn,
                thread_id,
                checkpoint_ns,
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                config if checkpoint_id else None,
            )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        params: list[Any] = []
        clauses: list[str] = []
        if config:
            clauses.append("thread_id = ?")
            params.append(config["configurable"]["thread_id"])
            if ns := config["configurable"].get("checkpoint_ns"):
                clauses.append("checkpoint_ns = ?")
                params.append(ns)

        before_id = get_checkpoint_id(before) if before else None
        if before_id:
            clauses.append("checkpoint_id < ?")
            params.append(before_id)

        sql = (
            "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
            "type, checkpoint, metadata FROM checkpoints"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY checkpoint_id DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        # Collect all tuples while the connection is open, then yield after close
        tuples: list[CheckpointTuple] = []
        async with aiosqlite.connect(self.db_path) as conn:
            await self._setup(conn)
            async with conn.execute(sql, params) as cur:
                rows = await cur.fetchall()

            for row in rows:
                t_id, ns, cp_id, parent_cp_id, cp_type, cp_blob, meta_blob = row
                if filter:
                    meta_type, meta_data = _unpack(bytes(meta_blob))
                    meta = self.serde.loads_typed((meta_type, meta_data))
                    if not all(meta.get(k) == v for k, v in filter.items()):
                        continue
                tuples.append(
                    await self._build_tuple(conn, t_id, ns, cp_id, parent_cp_id, cp_type, cp_blob, meta_blob)
                )

        for t in tuples:
            yield t

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")
        parent_checkpoint_id: str | None = get_checkpoint_id(config)

        c = checkpoint.copy()
        channel_values: dict[str, Any] = c.pop("channel_values", {})

        cp_type, cp_bytes = self.serde.dumps_typed(c)
        meta_type, meta_bytes = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        meta_blob = _pack(meta_type, meta_bytes)

        async with aiosqlite.connect(self.db_path) as conn:
            await self._setup(conn)
            for channel, version in new_versions.items():
                if channel in channel_values:
                    b_type, b_bytes = self.serde.dumps_typed(channel_values[channel])
                else:
                    b_type, b_bytes = "empty", b""
                await conn.execute(
                    "INSERT OR REPLACE INTO checkpoint_blobs "
                    "(thread_id, checkpoint_ns, channel, version, type, blob) VALUES (?,?,?,?,?,?)",
                    (thread_id, checkpoint_ns, channel, str(version), b_type, b_bytes),
                )
            await conn.execute(
                "INSERT OR REPLACE INTO checkpoints "
                "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint["id"],
                    parent_checkpoint_id,
                    cp_type,
                    cp_bytes,
                    meta_blob,
                ),
            )
            await conn.commit()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id: str = config["configurable"]["checkpoint_id"]

        async with aiosqlite.connect(self.db_path) as conn:
            await self._setup(conn)
            for idx, (channel, value) in enumerate(writes):
                inner_idx = WRITES_IDX_MAP.get(channel, idx)
                if inner_idx >= 0:
                    async with conn.execute(
                        "SELECT 1 FROM checkpoint_writes "
                        "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? AND task_id=? AND idx=?",
                        (thread_id, checkpoint_ns, checkpoint_id, task_id, inner_idx),
                    ) as cur:
                        if await cur.fetchone():
                            continue
                v_type, v_bytes = self.serde.dumps_typed(value)
                await conn.execute(
                    "INSERT OR REPLACE INTO checkpoint_writes "
                    "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob, task_path) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        task_id,
                        inner_idx,
                        channel,
                        v_type,
                        v_bytes,
                        task_path,
                    ),
                )
            await conn.commit()

    def get_next_version(self, current: str | None, channel: Any) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"


__all__ = ["HCodeSQLiteCheckpointer"]
