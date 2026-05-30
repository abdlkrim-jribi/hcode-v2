"""Unit tests for HCodeSQLiteCheckpointer."""
import uuid

import pytest

from deepagents.checkpointers.sqlite import HCodeSQLiteCheckpointer
from langgraph.checkpoint.base import empty_checkpoint, CheckpointMetadata


def _cfg(thread_id: str, checkpoint_id: str | None = None) -> dict:
    base = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    if checkpoint_id:
        base["configurable"]["checkpoint_id"] = checkpoint_id
    return base


def _checkpoint(msg: str = "hello", version: str = "1", checkpoint_id: str | None = None) -> dict:
    cp = empty_checkpoint()
    # Use caller-supplied ID or default to empty_checkpoint's time-ordered UUID v6.
    # Do NOT use uuid4 here — it is random and breaks ORDER BY checkpoint_id DESC.
    if checkpoint_id is not None:
        cp["id"] = checkpoint_id
    cp["channel_values"] = {"messages": msg}
    cp["channel_versions"] = {"messages": version}
    return cp


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


async def test_put_and_get_tuple(db_path):
    """Checkpoint saved with aput is retrieved correctly by aget_tuple."""
    saver = HCodeSQLiteCheckpointer(db_path=db_path)
    thread_id = "thread-1"
    cp = _checkpoint("hello")
    metadata: CheckpointMetadata = {"source": "input", "step": 1, "writes": {}, "parents": {}}

    config = _cfg(thread_id)
    stored_config = await saver.aput(config, cp, metadata, {"messages": "1"})

    result = await saver.aget_tuple(_cfg(thread_id))
    assert result is not None
    assert result.checkpoint["id"] == cp["id"]
    assert result.checkpoint["channel_values"]["messages"] == "hello"
    assert result.metadata["step"] == 1


async def test_new_session_starts_empty(db_path):
    """A thread with no checkpoints returns None from aget_tuple."""
    saver = HCodeSQLiteCheckpointer(db_path=db_path)
    result = await saver.aget_tuple(_cfg("brand-new-thread"))
    assert result is None


async def test_two_messages_in_order(db_path):
    """Two checkpoints saved to same thread are returned newest-first by alist."""
    saver = HCodeSQLiteCheckpointer(db_path=db_path)
    thread_id = "thread-order"
    metadata: CheckpointMetadata = {"source": "input", "step": 0, "writes": {}, "parents": {}}

    # Use lexicographically ordered IDs so ORDER BY checkpoint_id DESC is deterministic
    cp1 = _checkpoint("first", version="1", checkpoint_id="00000001-0000-0000-0000-000000000001")
    cp2 = _checkpoint("second", version="2", checkpoint_id="00000002-0000-0000-0000-000000000002")

    config = _cfg(thread_id)
    cfg1 = await saver.aput(config, cp1, {**metadata, "step": 1}, {"messages": "1"})
    await saver.aput(cfg1, cp2, {**metadata, "step": 2}, {"messages": "2"})

    results = [t async for t in saver.alist(_cfg(thread_id))]
    assert len(results) == 2
    # alist returns newest first
    assert results[0].checkpoint["channel_values"]["messages"] == "second"
    assert results[1].checkpoint["channel_values"]["messages"] == "first"


async def test_get_tuple_by_checkpoint_id(db_path):
    """aget_tuple retrieves a specific checkpoint when checkpoint_id is given."""
    saver = HCodeSQLiteCheckpointer(db_path=db_path)
    thread_id = "thread-byid"
    metadata: CheckpointMetadata = {"source": "input", "step": 1, "writes": {}, "parents": {}}

    cp = _checkpoint("specific")
    config = _cfg(thread_id)
    stored = await saver.aput(config, cp, metadata, {"messages": "1"})

    result = await saver.aget_tuple(stored)
    assert result is not None
    assert result.checkpoint["id"] == cp["id"]


async def test_put_writes_and_retrieve(db_path):
    """aput_writes stores pending writes that appear in aget_tuple.pending_writes."""
    saver = HCodeSQLiteCheckpointer(db_path=db_path)
    thread_id = "thread-writes"
    metadata: CheckpointMetadata = {"source": "input", "step": 1, "writes": {}, "parents": {}}

    cp = _checkpoint("base")
    config = _cfg(thread_id)
    stored = await saver.aput(config, cp, metadata, {"messages": "1"})

    await saver.aput_writes(stored, [("messages", "pending-value")], task_id="task-1")

    result = await saver.aget_tuple(_cfg(thread_id))
    assert result is not None
    channels = [w[1] for w in (result.pending_writes or [])]
    assert "messages" in channels
