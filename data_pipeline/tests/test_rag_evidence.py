import pytest
from pydantic import ValidationError

from bidpilot_data.schemas import Difficulty, QuestionType, QualityLevel, RAGQuestion, ReviewStatus


def test_answerable_requires_chunks():
    with pytest.raises(ValidationError):
        RAGQuestion(
            question_id="q1",
            project_id="p1",
            question="资格要求是什么？",
            answer="x",
            answerable=True,
            gold_chunk_ids=[],
            question_type=QuestionType.qualification,
            difficulty=Difficulty.easy,
            quality_level=QualityLevel.silver,
            review_status=ReviewStatus.pending,
        )


def test_unanswerable_ok():
    q = RAGQuestion(
        question_id="q2",
        project_id="p1",
        question="有月球验收标准吗？",
        answer=None,
        answerable=False,
        question_type=QuestionType.unanswerable,
        quality_level=QualityLevel.silver,
        review_status=ReviewStatus.pending,
    )
    assert q.answerable is False
