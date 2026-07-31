from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi.testclient import TestClient

from codex_workbench.config import Settings
from codex_workbench.main import create_app
from tests.helpers import build_settings


def _settings(
    tmp_path: Path,
    *,
    create_data: bool = True,
    environment: Literal["test", "production"] = "test",
) -> Settings:
    return build_settings(
        tmp_path,
        create_data=create_data,
        environment=environment,
    )


def test_liveness_is_public_and_minimal(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0", "checks": None}
    assert response.headers["cache-control"] == "no-store"


def test_request_id_is_server_generated_not_reflected_from_untrusted_input(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/api/health/live",
            headers={"X-Request-ID": "do-not-copy-this-user-controlled-value"},
        )

    request_id = response.headers["x-request-id"]
    assert request_id != "do-not-copy-this-user-controlled-value"
    assert str(UUID(request_id)) == request_id


def test_readiness_reports_missing_data_directory(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, create_data=False))) as client:
        response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["data"] is False
    assert response.json()["checks"]["security"] is False


def test_production_exposes_health_but_not_api_schema(tmp_path: Path) -> None:
    settings = _settings(tmp_path, environment="production")
    with TestClient(create_app(settings)) as client:
        health_response = client.get("/api/health/live")
        unauthenticated_schema_response = client.get("/api/openapi.json")
        client.post(
            "/api/auth/setup",
            json={"password": "correct horse battery staple"},
            headers={"Origin": settings.canonical_public_origin},
        )
        schema_response = client.get("/api/openapi.json")

    assert health_response.status_code == 200
    assert unauthenticated_schema_response.status_code == 401
    assert schema_response.status_code == 404
