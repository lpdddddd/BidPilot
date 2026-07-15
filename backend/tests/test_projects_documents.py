from app.models.enums import DocumentType, ParseStatus


def test_create_and_list_projects(client):
    create = client.post(
        "/api/v1/projects",
        json={
            "project_code": "API-001",
            "project_name": "接口测试项目",
            "purchaser": "测试采购人",
            "industry": "IT",
            "region": "华东",
            "organization_name": "API Test Org",
        },
    )
    assert create.status_code == 201, create.text
    project = create.json()
    assert project["project_code"] == "API-001"
    assert project["organization_id"]

    listed = client.get("/api/v1/projects")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] >= 1
    assert any(item["id"] == project["id"] for item in body["items"])

    detail = client.get(f"/api/v1/projects/{project['id']}")
    assert detail.status_code == 200
    assert detail.json()["project_name"] == "接口测试项目"


def test_create_document_metadata(client):
    project = client.post(
        "/api/v1/projects",
        json={
            "project_code": "DOC-001",
            "project_name": "文档测试项目",
            "organization_name": "Doc Test Org",
        },
    ).json()

    response = client.post(
        f"/api/v1/projects/{project['id']}/documents",
        json={
            "document_type": DocumentType.tender.value,
            "file_name": "tender.pdf",
            "mime_type": "application/pdf",
            "sha256": "a" * 64,
            "file_size": 1024,
            "parse_status": ParseStatus.pending.value,
        },
    )
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["file_name"] == "tender.pdf"
    assert doc["storage_bucket"]
    assert doc["storage_key"]

    listed = client.get(f"/api/v1/projects/{project['id']}/documents")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
