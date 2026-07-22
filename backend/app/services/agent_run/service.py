"""Agent run orchestration: start / execute / resume / retry with persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.checkpoint import (
    DbCheckpointStore,
    serialize_memory_saver,
)
from app.agent.graph import build_graph
from app.agent.nodes._helpers import AgentInterrupt, AgentRuntime, reset_runtime, set_runtime
from app.agent.state import GRAPH_VERSION, AgentState, empty_state
from app.models.agent import AgentRun, AgentStep, ToolCall
from app.models.enums import AgentRunStatus
from app.models.project import BidProject
from app.schemas.agent_run import (
    AgentEventItem,
    AgentEventsResponse,
    AgentResultResponse,
    AgentRunListResponse,
    AgentRunRead,
    AgentRunStartRequest,
    AgentStateRead,
)
from app.services.agent_run.events import record_step, record_tool_call, status_from_str
from app.tools.agent_tools import RetrievalFn


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_summary(state: AgentState) -> dict[str, Any]:
    return {
        "status": state.get("status"),
        "current_node": state.get("current_node"),
        "completed_nodes": list(state.get("completed_nodes") or []),
        "compliance_run_id": state.get("compliance_run_id"),
        "compliance_summary": state.get("compliance_summary") or {},
        "draft_ids": state.get("draft_ids") or [],
        "draft_finding_count": len(state.get("draft_findings") or []),
        "warnings": state.get("warnings") or [],
        "errors": state.get("errors") or [],
        "requirement_count": len(state.get("requirements") or []),
        "match_count": len(state.get("requirement_matches") or []),
        "citation_count": len(state.get("citations") or []),
        "critical_qualification": bool(state.get("critical_qualification")),
        "graph_version": state.get("graph_version") or GRAPH_VERSION,
    }


class AgentRunService:
    def __init__(
        self,
        db: Session,
        *,
        llm: Any | None = None,
        retrieval_fn: RetrievalFn | None = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.retrieval_fn = retrieval_fn
        self.checkpoints = DbCheckpointStore(db)

    def start_run(
        self,
        project_id: UUID,
        request: AgentRunStartRequest | None = None,
        *,
        idempotency_key: str | None = None,
        execute: bool = True,
    ) -> AgentRunRead:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")

        req = request or AgentRunStartRequest()
        if idempotency_key:
            existing = self.db.scalar(
                select(AgentRun).where(
                    AgentRun.project_id == project_id,
                    AgentRun.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return self._to_read(existing)

        run = AgentRun(
            organization_id=project.organization_id,
            project_id=project.id,
            status=AgentRunStatus.pending,
            intent=req.intent or "bid_analysis_loop",
            graph_version=GRAPH_VERSION,
            idempotency_key=idempotency_key,
            input_json={
                "user_request": req.user_request,
                "requested_requirement_ids": [
                    str(x) for x in (req.requested_requirement_ids or [])
                ],
                "selected_document_ids": [
                    str(x) for x in (req.selected_document_ids or [])
                ],
                "metadata": req.metadata or {},
            },
            current_node=None,
        )
        self.db.add(run)
        self.db.flush()

        state = empty_state(
            run_id=run.id,
            project_id=project.id,
            organization_id=project.organization_id,
            user_request=req.user_request or "",
            requested_requirement_ids=[
                str(x) for x in (req.requested_requirement_ids or [])
            ],
            selected_document_ids=[str(x) for x in (req.selected_document_ids or [])],
            metadata=req.metadata or {},
        )
        run.state_json = json.loads(json.dumps(state, default=str))
        run.started_at = _now()
        run.status = AgentRunStatus.running
        self.db.commit()
        self.db.refresh(run)

        if execute:
            return self.execute_run(run.id)
        return self._to_read(run)

    def execute_run(self, run_id: UUID) -> AgentRunRead:
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        state: AgentState = dict(run.state_json or {})  # type: ignore[assignment]
        if not state.get("run_id"):
            state = empty_state(
                run_id=run.id,
                project_id=run.project_id,
                organization_id=run.organization_id,
            )
        return self._continue_from_state(run.id, state, memory=MemorySaver())

    def resume_run(self, run_id: UUID) -> AgentRunRead:
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        # Idempotent: completed / blocked / cancelled runs return as-is.
        if run.status in {
            AgentRunStatus.completed,
            AgentRunStatus.completed_with_warnings,
            AgentRunStatus.blocked,
            AgentRunStatus.cancelled,
        }:
            return self._to_read(run)

        if run.status not in {
            AgentRunStatus.waiting_for_user,
            AgentRunStatus.running,
            AgentRunStatus.failed,
        }:
            return self._to_read(run)

        cp_state = self.checkpoints.load_state(str(run.id))
        # Prefer the richer of DB checkpoint vs run.state_json (seq-ordered
        # checkpoint should win; fall back to state_json when checkpoint missing).
        state: AgentState = dict(cp_state or run.state_json or {})  # type: ignore[assignment]
        if cp_state and run.state_json:
            cp_done = list(cp_state.get("completed_nodes") or [])
            run_done = list((run.state_json or {}).get("completed_nodes") or [])
            if len(run_done) > len(cp_done):
                state = dict(run.state_json)  # type: ignore[assignment]
        state.pop("lg_memory", None)  # type: ignore[arg-type]
        meta = dict(state.get("metadata") or {})
        # Clear interrupt flag so graph can continue; keep _interrupted so we don't
        # re-trigger the same interrupt_after_node.
        meta.pop("interrupt_after_node", None)
        state["metadata"] = meta
        state["interrupt_requested"] = False
        if state.get("status") == "waiting_for_user":
            state["status"] = "running"
        run.state_json = json.loads(json.dumps(state, default=str))
        run.status = AgentRunStatus.running
        self.db.commit()

        # Re-stream from START with completed_nodes skip. Use a fresh
        # MemorySaver so restored LG blobs cannot stall the graph; still
        # persist lg_memory on subsequent saves for observability.
        return self._continue_from_state(run.id, state, memory=MemorySaver())

    def retry_run(self, run_id: UUID) -> AgentRunRead:
        """Controlled retry on the same run_id.

        Semantics:
        - Increment ``metadata.retry_attempt`` and record ``metadata.retry_of_status``.
        - Clear error fields.
        - Remove ``current_node`` (failure point) from ``completed_nodes`` so it
          re-runs; keep earlier completed nodes so services are not duplicated.
        - Preserve compliance_run_id / draft_ids / business object IDs.
        """
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        state: AgentState = dict(run.state_json or {})  # type: ignore[assignment]
        state.pop("lg_memory", None)  # type: ignore[arg-type]
        meta = dict(state.get("metadata") or {})
        meta["retry_attempt"] = int(meta.get("retry_attempt") or 0) + 1
        meta["retry_of_status"] = (
            run.status.value if hasattr(run.status, "value") else str(run.status)
        )
        state["metadata"] = meta
        state["errors"] = []
        state["error_code"] = None
        state["error_summary"] = None
        state["last_error_retryable"] = False
        state["status"] = "running"
        state["interrupt_requested"] = False

        failed_node = state.get("current_node") or run.current_node
        if failed_node:
            state["completed_nodes"] = [
                n for n in (state.get("completed_nodes") or []) if n != failed_node
            ]

        run.state_json = json.loads(json.dumps(state, default=str))
        run.status = AgentRunStatus.running
        run.error_code = None
        run.error_summary = None
        run.error_message = None
        self.db.commit()

        # Re-stream from START with completed_nodes skip. Use a fresh
        # MemorySaver so restored LG blobs cannot stall the graph; still
        # persist lg_memory on subsequent saves for observability.
        return self._continue_from_state(run.id, state, memory=MemorySaver())

    def _continue_from_state(
        self,
        run_id: UUID,
        state: AgentState,
        *,
        memory: MemorySaver | None = None,
    ) -> AgentRunRead:
        """Stream the graph with stable ``thread_id=str(run.id)``.

        Completed nodes short-circuit via ``completed_nodes`` so services are not
        re-invoked on resume / controlled retry.
        """
        run = self.db.get(AgentRun, run_id)
        assert run is not None
        memory = memory or MemorySaver()
        graph = build_graph(checkpointer=memory)
        # Stable thread id for start, resume, and retry — never random.
        thread_id = str(run.id)
        config = {"configurable": {"thread_id": thread_id}}

        runtime = AgentRuntime(
            db=self.db,
            llm=self.llm,
            retrieval_fn=self.retrieval_fn,
            persist_tool=lambda **kw: record_tool_call(
                self.db,
                agent_run_id=run.id,
                **{k: v for k, v in kw.items() if k != "run_id"},
            ),
            save_checkpoint=lambda st, node: self.checkpoints.save(
                agent_run_id=run.id,
                thread_id=thread_id,
                node_name=node,
                state=st,
                lg_memory=serialize_memory_saver(memory),
            ),
        )
        token = set_runtime(runtime)
        try:
            run.status = AgentRunStatus.running
            run.started_at = run.started_at or _now()
            self.db.flush()

            final_state = state
            try:
                for event in graph.stream(state, config=config, stream_mode="updates"):
                    for node_name, partial in event.items():
                        if isinstance(partial, dict):
                            final_state = {**final_state, **partial}  # type: ignore[misc]
                        self._after_node(run, node_name, final_state, memory=memory)
                        if final_state.get("interrupt_requested"):
                            raise AgentInterrupt(node_name)
            except AgentInterrupt as exc:
                if runtime.last_state:
                    final_state = {**final_state, **runtime.last_state}  # type: ignore[misc]
                final_state["status"] = "waiting_for_user"
                final_state["current_node"] = exc.node
                final_state["interrupt_requested"] = True
                self._after_node(run, exc.node, final_state, memory=memory)
                self._persist_run(run, final_state)
                self.db.commit()
                self.db.refresh(run)
                return self._to_read(run)

            self._persist_run(run, final_state)
            self.db.commit()
            self.db.refresh(run)
            return self._to_read(run)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            state["status"] = "failed"
            state["error_code"] = "execute_error"
            state["error_summary"] = f"{type(exc).__name__}: {exc}"
            self._persist_run(run, state)
            self.db.commit()
            self.db.refresh(run)
            return self._to_read(run)
        finally:
            reset_runtime(token)

    def _after_node(
        self,
        run: AgentRun,
        node_name: str,
        state: AgentState,
        *,
        memory: MemorySaver | None = None,
    ) -> None:
        record_step(
            self.db,
            agent_run_id=run.id,
            node_name=node_name,
            status="succeeded" if state.get("status") != "failed" else "failed",
            output_json={
                "status": state.get("status"),
                "warnings": state.get("warnings"),
                "route_decision": state.get("route_decision"),
                "completed_nodes": state.get("completed_nodes"),
            },
            error_message=state.get("error_summary"),
        )
        lg_memory = serialize_memory_saver(memory) if memory is not None else None
        self.checkpoints.save(
            agent_run_id=run.id,
            thread_id=str(run.id),
            node_name=node_name,
            state=dict(state),
            next_node=None,
            lg_memory=lg_memory,
        )
        run.current_node = node_name
        run.state_json = json.loads(json.dumps(state, default=str))
        run.status = status_from_str(str(state.get("status") or "running"))
        self.db.flush()

    def _persist_run(self, run: AgentRun, state: AgentState) -> None:
        run.state_json = json.loads(json.dumps(state, default=str))
        run.current_node = state.get("current_node")
        run.graph_version = state.get("graph_version") or GRAPH_VERSION
        run.output_summary_json = _safe_summary(state)
        run.status = status_from_str(str(state.get("status") or "running"))
        run.error_code = state.get("error_code")
        run.error_summary = state.get("error_summary")
        run.error_message = state.get("error_summary")
        if run.status in {
            AgentRunStatus.completed,
            AgentRunStatus.completed_with_warnings,
            AgentRunStatus.blocked,
            AgentRunStatus.failed,
            AgentRunStatus.cancelled,
        }:
            run.finished_at = _now()
        self.db.flush()

    def get_run(self, run_id: UUID, *, project_id: UUID | None = None) -> AgentRunRead:
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        if project_id is not None and run.project_id != project_id:
            raise HTTPException(status_code=404, detail="agent run not found")
        return self._to_read(run)

    def get_events(self, run_id: UUID, *, project_id: UUID | None = None) -> AgentEventsResponse:
        run = self.get_run(run_id, project_id=project_id)
        steps = list(
            self.db.scalars(
                select(AgentStep)
                .where(AgentStep.agent_run_id == run_id)
                .order_by(AgentStep.step_index.asc())
            ).all()
        )
        tools = list(
            self.db.scalars(
                select(ToolCall)
                .where(ToolCall.agent_run_id == run_id)
                .order_by(ToolCall.created_at.asc())
            ).all()
        )
        events: list[AgentEventItem] = []
        for s in steps:
            events.append(
                AgentEventItem(
                    event_type="step",
                    sequence=s.step_index,
                    name=s.node_name,
                    status=s.status,
                    summary=(s.output_json or {}).get("status")
                    if isinstance(s.output_json, dict)
                    else None,
                    created_at=s.created_at,
                    payload={"output": s.output_json, "error": s.error_message},
                )
            )
        for i, t in enumerate(tools):
            summary = None
            if isinstance(t.result_json, dict):
                summary = t.result_json.get("summary")
            events.append(
                AgentEventItem(
                    event_type="tool",
                    sequence=10_000 + i,
                    name=t.tool_name,
                    status=t.status,
                    summary=summary or t.error_message,
                    created_at=t.created_at,
                    payload={"duration_ms": t.duration_ms},
                )
            )
        events.sort(key=lambda e: (e.created_at or datetime.min.replace(tzinfo=UTC), e.sequence))
        return AgentEventsResponse(run_id=run.id, items=events, total=len(events))

    def get_result(self, run_id: UUID, *, project_id: UUID | None = None) -> AgentResultResponse:
        run = self.db.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="agent run not found")
        if project_id is not None and run.project_id != project_id:
            raise HTTPException(status_code=404, detail="agent run not found")
        state = dict(run.state_json or {})
        return AgentResultResponse(
            run=self._to_read(run),
            summary=run.output_summary_json or _safe_summary(state),  # type: ignore[arg-type]
            state=AgentStateRead.model_validate(state) if state else None,
            citations=list(state.get("citations") or []),
            draft_ids=[UUID(x) for x in (state.get("draft_ids") or []) if x],
            compliance_run_id=(
                UUID(state["compliance_run_id"])
                if state.get("compliance_run_id")
                else None
            ),
            warnings=list(state.get("warnings") or []),
            errors=list(state.get("errors") or []),
        )

    def list_for_project(
        self, project_id: UUID, *, limit: int = 20
    ) -> AgentRunListResponse:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        rows = list(
            self.db.scalars(
                select(AgentRun)
                .where(AgentRun.project_id == project_id)
                .order_by(AgentRun.created_at.desc())
                .limit(limit)
            ).all()
        )
        return AgentRunListResponse(
            items=[self._to_read(r) for r in rows],
            total=len(rows),
        )

    def get_latest(self, project_id: UUID) -> AgentRunRead | None:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        row = self.db.scalar(
            select(AgentRun)
            .where(AgentRun.project_id == project_id)
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        return self._to_read(row) if row else None

    def _to_read(self, run: AgentRun) -> AgentRunRead:
        return AgentRunRead(
            id=run.id,
            organization_id=run.organization_id,
            project_id=run.project_id,
            status=run.status,
            intent=run.intent,
            current_node=run.current_node,
            graph_version=run.graph_version or GRAPH_VERSION,
            idempotency_key=run.idempotency_key,
            input_json=run.input_json,
            output_summary_json=run.output_summary_json,
            error_code=run.error_code,
            error_summary=run.error_summary or run.error_message,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
            state=AgentStateRead.model_validate(run.state_json)
            if run.state_json
            else None,
        )
