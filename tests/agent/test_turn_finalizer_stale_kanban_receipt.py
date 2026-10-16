"""Run-ownership regression for the turn finalizer's terminal Kanban receipt."""

from __future__ import annotations

import logging
from pathlib import Path

from agent.turn_finalizer import _record_kanban_budget_exhausted
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


def test_stale_budget_receipt_cannot_mutate_newer_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))

    with kbc.connect(db_path) as conn:
        task_id = kb.create_task(conn, title="stale terminal receipt", assignee="builder")
        implementation = kb.claim_task(conn, task_id, claimer="builder:old")
        assert implementation is not None
        old_run_id = implementation.current_run_id
        assert old_run_id is not None
        assert kb.request_review(
            conn,
            task_id,
            summary="ready",
            reviewer="reviewer",
            expected_run_id=old_run_id,
        )
        review = kb.claim_review_task(conn, task_id, claimer="reviewer:new")
        assert review is not None
        new_run_id = review.current_run_id
        assert new_run_id is not None and new_run_id != old_run_id

    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(old_run_id))
    _record_kanban_budget_exhausted(
        task_id,
        api_call_count=60,
        max_iterations=60,
        logger=logging.getLogger(__name__),
    )

    with kbc.connect(db_path) as conn:
        task = kb.get_task(conn, task_id)
        assert task is not None
        assert task.status == "running"
        assert task.current_run_id == new_run_id
        assert task.claim_lock == "reviewer:new"
        assert task.consecutive_failures == 0
        current_run = conn.execute(
            "SELECT ended_at, outcome FROM task_runs WHERE id = ?",
            (new_run_id,),
        ).fetchone()
        assert current_run is not None
        assert current_run["ended_at"] is None
        assert current_run["outcome"] is None
