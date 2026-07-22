"""SSE stream catch-up, Last-Event-ID, and terminal close."""

from __future__ import annotations

from uuid import uuid4

from app.models import BidProject, Organization
from app.models.agent import AgentRun
from app.models.enums import AgentRunStatus
from app.services.agent_run.events import record_event, record_node_started
from app.services.agent_run.sse import iter_agent_events_sse, stream_agent_events_sse
from sqlalchemy.orm import Session


def _seed(db: Session):
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"SSE-{uuid4().hex[:4]}",
        project_name="SSE",
    )
    db.add(project)
    db.flush()
    return project


def test_sse_replays_and_closes_on_terminal(db: Session):
    project = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.completed,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
        event_sequence=0,
    )
    db.add(run)
    db.flush()
    record_node_started(db, agent_run_id=run.id, node_name="initialize_run")
    record_event(
        db,
        agent_run_id=run.id,
        event_type="run_completed",
        status="completed",
        safe_summary="done",
    )
    db.commit()

    chunks = list(iter_agent_events_sse(run.id, project_id=project.id, after_sequence=-1))
    text = "".join(chunks)
    assert "event: agent_event" in text
    assert "id: 0" in text
    assert "event: done" in text
    assert "run_status" in text


def test_sse_after_sequence_skips_history(db: Session):
    project = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.completed,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
    )
    db.add(run)
    db.flush()
    record_event(db, agent_run_id=run.id, event_type="node_started", node_name="a")
    record_event(db, agent_run_id=run.id, event_type="node_completed", node_name="a")
    record_event(db, agent_run_id=run.id, event_type="run_completed", status="completed")
    db.commit()

    text = "".join(iter_agent_events_sse(run.id, project_id=project.id, after_sequence=0))
    assert '"sequence": 0' not in text.replace(" ", "")
    assert "event: done" in text


def test_sse_cross_project_denied(db: Session):
    project = _seed(db)
    other = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.completed,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
    )
    db.add(run)
    db.commit()
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        stream_agent_events_sse(run.id, project_id=other.id)
    assert ei.value.status_code == 404
