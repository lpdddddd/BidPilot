def _preflight(client, origin: str):
    return client.options(
        "/api/v1/projects",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )


def test_preflight_allows_configured_dev_origin(client_no_db):
    response = _preflight(client_no_db, "http://localhost:5173")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    # No cookie/session auth yet: wildcard + credentials must never combine.
    assert response.headers.get("access-control-allow-credentials") != "true"
    assert response.headers["access-control-allow-origin"] != "*" or (
        response.headers.get("access-control-allow-credentials") != "true"
    )


def test_preflight_rejects_unknown_origin(client_no_db):
    response = _preflight(client_no_db, "http://evil.example.com")
    assert "access-control-allow-origin" not in response.headers


def test_default_origins_are_explicit_not_wildcard():
    from app.core.config import Settings

    origins = Settings(cors_origins="http://localhost:5173,http://127.0.0.1:5173").cors_origins_list
    assert "*" not in origins
    assert "http://localhost:5173" in origins
    assert "http://127.0.0.1:5173" in origins
