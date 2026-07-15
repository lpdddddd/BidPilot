import pytest

from bidpilot_data.collectors.official_source_validator import validate_official_source
from bidpilot_data.collectors.source_registry import load_source_registry
from bidpilot_data.labeling.synthetic_companies import SyntheticDataForbiddenError, build_synthetic_companies_and_matches
from bidpilot_data.collectors.metadata_extractor import is_guangdong_text


def test_whitelist_accepts_ccgp_and_rejects_aggregator():
    reg = load_source_registry()
    assert validate_official_source("https://www.ccgp.gov.cn/cggg/zygg/gkzb/", reg).ok
    assert validate_official_source("https://download.ccgp.gov.cn/oss/download?uuid=ABC", reg).ok
    assert not validate_official_source("https://www.bidder-aggregator.example/tender/1", reg).ok


def test_guangdong_filter_rejects_zhongshan_hospital_shanghai():
    assert is_guangdong_text("中山大学深圳校区信息化项目")
    assert not is_guangdong_text("复旦大学附属中山医院数字化X射线系统")
    assert not is_guangdong_text("山东第一医科大学大数据平台运维项目")


def test_synthetic_generation_disabled():
    with pytest.raises(SyntheticDataForbiddenError):
        build_synthetic_companies_and_matches()
