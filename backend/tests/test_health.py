from pathlib import Path
from typing import Literal

from fastapi.testclient import TestClient

from codex_workbench.config import Settings
from codex_workbench.main import create_app


def _settings(
    tmp_path: Path,
    *,
    create_data: bool = True,
    environment: Literal["test", "production"] = "test",
) -> Settings:
    workspace = tmp_path / "workspace"
    repositories = workspace / "repositories"
    data = tmp_path / "data"
    repositories.mkdir(parents=True)
    if create_data:
        data.mkdir()
    return Settings(
        _env_file=None,
        environment=environment,
        workspace_root=workspace,
        repositories_root=repositories,
        data_dir=data,
        uploads_dir=data / "uploads",
    )


def test_liveness_is_public_and_minimal(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0", "checks": None}
    assert response.headers["cache-control"] == "no-store"


def test_readiness_reports_missing_data_directory(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, create_data=False))) as client:
        response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["data"] is False


def test_production_exposes_health_but_not_api_schema(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, environment="production"))) as client:
        health_response = client.get("/api/health/live")
        schema_response = client.get("/api/openapi.json")

    assert health_response.status_code == 200
    assert schema_response.status_code == 404
