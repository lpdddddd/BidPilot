from unittest.mock import patch

from app.services.infra_clients import CheckResult


def test_health_ok(client_no_db):
    response = client_no_db.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_services(client):
    with (
        patch(
            "app.services.health.check_postgres",
            return_value=CheckResult(name="postgres", ok=True),
        ),
        patch(
            "app.services.health.check_redis",
            return_value=CheckResult(name="redis", ok=True),
        ),
        patch(
            "app.services.health.check_minio",
            return_value=CheckResult(name="minio", ok=False, detail="down"),
        ),
        patch(
            "app.services.health.check_qdrant",
            return_value=CheckResult(name="qdrant", ok=True),
        ),
    ):
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    names = {item["name"]: item["status"] for item in body["services"]}
    assert names["postgres"] == "ok"
    assert names["minio"] == "error"
