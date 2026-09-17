"""Research Run Ledger.

Tracks full lifecycle state of research runs, tasks, evidence manifests,
and answer versions with SQLite persistence and latency telemetry.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from .models import (
    ResearchRun,
    ResearchIntent,
    ResearchBudget,
    ResearchTask,
    EvidenceItem,
    AnswerVersion,
    RunStatus,
)


class ResearchLedger:
    """Manages active research runs with in-memory fast state and SQLite persistence."""

    def __init__(self):
        self._active_runs: Dict[str, ResearchRun] = {}

    def create_run(self, session_id: str, message_id: str,
                   intent: ResearchIntent, budget: ResearchBudget) -> ResearchRun:
        import app.database as db

        run_id = f"rr_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        fg_deadline = datetime.fromtimestamp(
            time.time() + (budget.foreground_deadline_ms / 1000.0), timezone.utc
        ).isoformat()
        bg_deadline = datetime.fromtimestamp(
            time.time() + (budget.background_deadline_ms / 1000.0), timezone.utc
        ).isoformat()

        run = ResearchRun(
            run_id=run_id,
            session_id=session_id,
            message_id=message_id,
            intent=intent,
            budget=budget,
            status="foreground_running",
            created_at=now,
            foreground_deadline_at=fg_deadline,
            background_deadline_at=bg_deadline,
            metrics={"t_start": time.time()},
        )
        self._active_runs[run_id] = run
        try:
            db.save_research_run(run.to_dict())
        except Exception:
            pass
        return run

    def record_task(self, run_id: str, task: ResearchTask) -> None:
        import app.database as db

        run = self._active_runs.get(run_id)
        if run:
            # Update existing task or append
            for i, existing in enumerate(run.tasks):
                if existing.task_id == task.task_id:
                    run.tasks[i] = task
                    break
            else:
                run.tasks.append(task)
        try:
            db.save_research_task(task.to_dict(), run_id)
        except Exception:
            pass

    def record_evidence(self, run_id: str, item: EvidenceItem) -> None:
        import app.database as db

        run = self._active_runs.get(run_id)
        if run:
            run.evidence[item.evidence_id] = item
        try:
            db.save_research_evidence(item.to_dict(), run_id)
        except Exception:
            pass

    def record_answer_version(self, run_id: str, answer: AnswerVersion) -> None:
        import app.database as db

        run = self._active_runs.get(run_id)
        if run:
            run.answer_versions.append(answer)
            if answer.version == 1:
                run.metrics["t_partial"] = time.time() - run.metrics.get("t_start", time.time())
            else:
                run.metrics["t_final"] = time.time() - run.metrics.get("t_start", time.time())
        try:
            db.save_research_answer_version(answer.to_dict(), run_id)
        except Exception:
            pass

    def update_status(self, run_id: str, status: RunStatus,
                      metrics_extra: Optional[Dict[str, Any]] = None) -> None:
        import app.database as db

        run = self._active_runs.get(run_id)
        completed_at = None
        if status in ("completed", "cancelled", "partial_final", "failed"):
            completed_at = datetime.now(timezone.utc).isoformat()
            if run:
                run.metrics["t_total"] = time.time() - run.metrics.get("t_start", time.time())

        if run:
            run.status = status
            if metrics_extra:
                run.metrics.update(metrics_extra)

        try:
            db.update_research_run_status(
                run_id=run_id,
                status=status,
                completed_at=completed_at,
                metrics=run.metrics if run else metrics_extra,
            )
        except Exception:
            pass

        if status in ("completed", "cancelled", "partial_final", "failed"):
            self._active_runs.pop(run_id, None)

    def get_run(self, run_id: str) -> Optional[ResearchRun]:
        return self._active_runs.get(run_id)


# Global ledger instance
research_ledger = ResearchLedger()
