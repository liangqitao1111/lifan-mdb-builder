# -*- coding: utf-8 -*-
"""D1/D2 regression tests for transactional write-back and undo ordering."""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import db  # noqa: E402


def test_insert_readback_mismatch_rolls_back(tmp_path):
    path = str(tmp_path / "insert-trigger.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT);
        CREATE TRIGGER normalize AFTER INSERT ON t BEGIN
          UPDATE t SET value = 'mutated' WHERE id = new.id;
        END;
        """
    )
    conn.commit()
    conn.close()

    result, undo = db.batch_apply(
        path,
        [{"op": "insert", "table": "t", "data": {"value": "requested"}}],
        verify=True,
    )

    assert not result["ok"]
    assert result["failures"][0]["field"] == "value"
    assert undo == []
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_undo_runs_in_reverse_and_restores_stack_state(tmp_path):
    path = str(tmp_path / "undo-order.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT)")
    conn.execute("INSERT INTO t(value) VALUES ('original')")
    conn.commit()
    conn.close()

    result, undo = db.batch_apply(
        path,
        [
            {"op": "update", "table": "t", "row_id": 1, "data": {"value": "changed"}},
            {"op": "delete", "table": "t", "row_id": 1},
        ],
        verify=True,
    )
    assert result["ok"]
    restored = db.apply_undo_ops(path, undo, verify=True)
    assert restored["ok"]
    assert sqlite3.connect(path).execute("SELECT value FROM t WHERE id=1").fetchone()[0] == "original"
