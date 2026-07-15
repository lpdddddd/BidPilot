from bidpilot_data.collectors.download import collect_from_manifest, deduplicate_raw
from bidpilot_data.utils import sha256_bytes, write_jsonl


def test_sha256_dedup_and_file_collect(tmp_datasets, tmp_repo):
    raw = tmp_repo / "fixture.txt"
    raw.write_text("hello bidpilot fixture", encoding="utf-8")
    digest = sha256_bytes(raw.read_bytes())
    manifest = tmp_datasets / "manifests" / "source_manifest.jsonl"
    write_jsonl(
        manifest,
        [
            {
                "source_id": "s1",
                "source_url": raw.resolve().as_uri(),
                "source_site": "local",
                "project_code": "P1",
                "project_name": "Proj1",
                "document_type": "tender",
                "license_or_terms": "fixture",
            },
            {
                "source_id": "s2",
                "source_url": raw.resolve().as_uri(),
                "source_site": "local",
                "project_code": "P1",
                "project_name": "Proj1",
                "document_type": "tender",
                "license_or_terms": "fixture",
            },
        ],
    )
    stats = collect_from_manifest(manifest, resume=False)
    assert stats["downloaded"] == 1
    assert stats["duplicates"] == 1
    report = deduplicate_raw()
    assert report["unique_documents"] == 1
    assert digest
