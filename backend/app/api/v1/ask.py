"""Grounded ask API: evidence-bound RAG answers with optional SSE."""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ask import AskRequest, AskResponse
from app.services.rag_answer_service import RagAnswerService

router = APIRouter()


def _sse_pack(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post(
    "/{project_id}/ask",
    response_model=None,
    summary="Grounded RAG ask (JSON or SSE)",
)
def ask_project(
    project_id: UUID,
    payload: AskRequest,
    db: Session = Depends(get_db),
) -> AskResponse | StreamingResponse:
    service = RagAnswerService(db)
    if not payload.stream:
        return service.answer(project_id, payload)

    def event_iter() -> Iterator[str]:
        for item in service.answer_stream(project_id, payload):
            yield _sse_pack(item["event"], item["data"])

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
