from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi.testclient import TestClient

from codex_workbench.config import Settings
from codex_workbench.main import create_app
from codex_workbench.runtime import WorkbenchRuntime
from tests.helpers import build_settings, executable_fixture

_FIXTURE = Path(__file__).parent / "fixtures" / "fake_codex.py"


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
    assert response.json() == {"status": "ok", "version": "0.2.2", "checks": None}
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


def _app_server_settings(tmp_path: Path, executable: Path) -> Settings:
    base = _settings(tmp_path)
    values = base.model_dump()
    values.update(
        {
            "codex_executable": str(executable),
            "app_server_start_enabled": True,
        }
    )
    return Settings(_env_file=None, **values)


def test_readiness_is_200_after_supported_app_server_initializes(tmp_path: Path) -> None:
    with TestClient(create_app(_app_server_settings(tmp_path, _FIXTURE))) as client:
        response = client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["app_server"] is True


def test_readiness_is_503_after_version_check_fails(tmp_path: Path) -> None:
    incompatible = executable_fixture("incompatible-codex", "printf '%s\\n' 'codex-cli 0.147.0'")
    app = create_app(_app_server_settings(tmp_path, incompatible))
    with TestClient(app) as client:
        response = client.get("/api/health/ready")
        runtime = app.state.runtime

    assert response.status_code == 503
    assert response.json()["checks"]["app_server"] is False
    assert isinstance(runtime, WorkbenchRuntime)
    assert runtime.supervisor.status.error_code == "unsupported_version"


def test_production_exposes_health_but_not_api_schema(tmp_path: Path) -> None:
    settings = _settings(tmp_path, environment="production")
    with TestClient(create_app(settings)) as client:
        health_response = client.get("/api/health/live")
        proxy_headers = {
            "Host": settings.public_authority,
            "X-Forwarded-Host": settings.public_authority,
            "X-Forwarded-Proto": settings.public_scheme,
        }
        unauthenticated_schema_response = client.get("/api/openapi.json", headers=proxy_headers)
        client.post(
            "/api/auth/setup",
            json={"password": "correct horse battery staple"},
            headers={"Origin": settings.canonical_public_origin, **proxy_headers},
        )
        schema_response = client.get("/api/openapi.json", headers=proxy_headers)

    assert health_response.status_code == 200
    assert unauthenticated_schema_response.status_code == 401
    assert schema_response.status_code == 404
