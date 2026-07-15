import pytest
from pydantic import ValidationError

from bidpilot_data.schemas import QualityLevel, RequirementAnnotation, ReviewStatus, TaxonomyCategory


def test_requirement_schema_ok():
    ann = RequirementAnnotation(
        annotation_id="a1",
        requirement_id="r1",
        project_id="p1",
        category=TaxonomyCategory.qualification,
        title="营业执照",
        original_text="投标人须提供有效营业执照。",
        normalized_requirement="提供有效营业执照",
        quality_level=QualityLevel.silver,
        review_status=ReviewStatus.pending,
    )
    assert ann.mandatory is False


def test_gold_requires_reviewer():
    with pytest.raises(ValidationError):
        RequirementAnnotation(
            annotation_id="a1",
            requirement_id="r1",
            project_id="p1",
            category=TaxonomyCategory.qualification,
            title="x",
            original_text="x",
            normalized_requirement="x",
            quality_level=QualityLevel.gold,
            review_status=ReviewStatus.reviewed,
            reviewer=None,
        )
