from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from codex_workbench.config import Settings
from codex_workbench.main import create_app
from codex_workbench.security.rate_limit import LoginRateLimiter
from tests.helpers import build_settings

_PASSWORD = "correct horse battery staple"


def _origin_headers(settings: Settings) -> dict[str, str]:
    return {
        "Origin": settings.canonical_public_origin,
        "Sec-Fetch-Site": "same-origin",
    }


def _setup(client: TestClient, settings: Settings) -> dict[str, object]:
    response = client.post(
        "/api/auth/setup",
        json={"password": _PASSWORD},
        headers=_origin_headers(settings),
    )
    assert response.status_code == 200
    return cast("dict[str, object]", response.json())


def test_bootstrap_creates_argon2id_verifier_and_closes_setup(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/auth/status").json()["state"] == "setup_required"
        result = _setup(client, settings)
        repeated = client.post(
            "/api/auth/setup",
            json={"password": "another password value"},
            headers=_origin_headers(settings),
        )

    verifier = settings.password_verifier_file.read_text(encoding="ascii")
    assert result["state"] == "authenticated"
    assert result["csrfToken"]
    assert verifier.startswith("$argon2id$")
    assert _PASSWORD not in verifier
    assert settings.password_verifier_file.stat().st_mode & 0o077 == 0
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "setup_unavailable"


def test_setup_cookie_and_database_contain_no_reusable_session_token(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/auth/setup",
            json={"password": _PASSWORD},
            headers=_origin_headers(settings),
        )
        raw_token = client.cookies.get(settings.session_cookie_name)

    assert raw_token is not None
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/" in set_cookie
    assert "Secure" not in set_cookie

    with sqlite3.connect(settings.database_path) as connection:
        token_digest, revoked_at = connection.execute(
            "SELECT token_digest, revoked_at FROM sessions"
        ).fetchone()
    assert isinstance(token_digest, bytes)
    assert len(token_digest) == 32
    assert raw_token.encode() not in settings.database_path.read_bytes()
    assert revoked_at is None


def test_secure_cookie_uses_host_prefix(tmp_path: Path) -> None:
    settings = build_settings(
        tmp_path,
        public_origin="https://workbench.example",
        secure_cookie=True,
    )
    with TestClient(create_app(settings), base_url="https://workbench.example") as client:
        response = client.post(
            "/api/auth/setup",
            json={"password": _PASSWORD},
            headers=_origin_headers(settings),
        )

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("__Host-codex_workbench_session=")
    assert "Secure" in set_cookie
    assert "Domain=" not in set_cookie


def test_login_uses_generic_error_and_rotates_existing_session(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        _setup(client, settings)
        first_token = client.cookies.get(settings.session_cookie_name)
        failure = client.post(
            "/api/auth/login",
            json={"password": "not the password"},
            headers=_origin_headers(settings),
        )
        success = client.post(
            "/api/auth/login",
            json={"password": _PASSWORD},
            headers=_origin_headers(settings),
        )
        second_token = client.cookies.get(settings.session_cookie_name)

    assert failure.status_code == 401
    assert failure.json() == {
        "error": {"code": "invalid_credentials", "message": "Anmeldung fehlgeschlagen."}
    }
    assert success.status_code == 200
    assert first_token is not None and second_token is not None
    assert first_token != second_token

    with sqlite3.connect(settings.database_path) as connection:
        rows = connection.execute(
            "SELECT revoked_at FROM sessions ORDER BY created_at, rowid"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] is not None
    assert rows[1][0] is None


def test_request_validation_never_echoes_password(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    password = "short-secret-value"
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/auth/login",
            json={"password": ""},
            headers=_origin_headers(settings),
        )
        malformed = client.post(
            "/api/auth/setup",
            json={"password": password[:5]},
            headers=_origin_headers(settings),
        )

    assert response.status_code == 422
    assert malformed.status_code == 422
    assert password not in response.text
    assert password[:5] not in malformed.text


def test_actual_chunked_json_bytes_are_limited_without_content_length(
    tmp_path: Path,
) -> None:
    base = build_settings(tmp_path)
    values = base.model_dump()
    values["max_json_request_bytes"] = 1024
    settings = Settings(_env_file=None, **values)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/auth/login",
            content=iter([b'{"password":"', b"x" * 1200, b'"}']),
            headers={
                "Origin": settings.canonical_public_origin,
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_logout_requires_origin_and_csrf_then_revokes_session(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        setup = _setup(client, settings)
        csrf_token = str(setup["csrfToken"])
        no_csrf = client.post(
            "/api/auth/logout",
            json={},
            headers=_origin_headers(settings),
        )
        wrong_origin = client.post(
            "/api/auth/logout",
            json={},
            headers={"Origin": "http://attacker.example", "X-CSRF-Token": csrf_token},
        )
        logout = client.post(
            "/api/auth/logout",
            json={},
            headers={**_origin_headers(settings), "X-CSRF-Token": csrf_token},
        )
        status = client.get("/api/auth/status")

    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "csrf_rejected"
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["error"]["code"] == "request_origin_rejected"
    assert logout.status_code == 200
    assert status.json()["state"] == "unauthenticated"


def test_origin_is_required_for_setup_and_login(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        setup = client.post("/api/auth/setup", json={"password": _PASSWORD})
        login = client.post(
            "/api/auth/login",
            json={"password": _PASSWORD},
            headers={"Origin": "http://attacker.example"},
        )

    assert setup.status_code == 403
    assert login.status_code == 403


def test_non_public_api_fails_closed_without_session(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/openapi.json")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_expired"


def test_login_rate_limit_is_applied_per_address(tmp_path: Path) -> None:
    settings = build_settings(tmp_path, login_attempts=2, global_login_attempts=10)
    with TestClient(create_app(settings)) as client:
        _setup(client, settings)
        responses = [
            client.post(
                "/api/auth/login",
                json={"password": "incorrect password"},
                headers=_origin_headers(settings),
            )
            for _ in range(3)
        ]

    assert [response.status_code for response in responses] == [401, 401, 429]
    assert responses[-1].json()["error"]["code"] == "login_rate_limited"
    assert int(responses[-1].headers["retry-after"]) > 0


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_session_idle_expiry_is_server_side(tmp_path: Path) -> None:
    clock = MutableClock(1_000)
    settings = build_settings(tmp_path, idle_timeout=300, absolute_timeout=900)
    with TestClient(create_app(settings, wall_clock=clock)) as client:
        _setup(client, settings)
        clock.value = 1_301
        status = client.get("/api/auth/status")

    assert status.status_code == 200
    assert status.json()["state"] == "unauthenticated"
    assert status.json()["reason"] == "session_expired"


def test_session_absolute_expiry_cannot_be_extended_by_activity(tmp_path: Path) -> None:
    clock = MutableClock(1_000)
    settings = build_settings(tmp_path, idle_timeout=300, absolute_timeout=600)
    with TestClient(create_app(settings, wall_clock=clock)) as client:
        _setup(client, settings)
        clock.value = 1_200
        assert client.get("/api/auth/status").json()["state"] == "authenticated"
        clock.value = 1_400
        assert client.get("/api/auth/status").json()["state"] == "authenticated"
        clock.value = 1_601
        status = client.get("/api/auth/status")

    assert status.json()["state"] == "unauthenticated"
    assert status.json()["reason"] == "session_expired"


def test_login_rate_limiter_has_a_global_boundary() -> None:
    clock = MutableClock(10)
    limiter = LoginRateLimiter(
        address_limit=2,
        global_limit=2,
        window_seconds=60,
        clock=clock,
    )

    assert limiter.consume("address-a").allowed is True
    assert limiter.consume("address-b").allowed is True
    decision = limiter.consume("address-c")
    assert decision.allowed is False
    assert decision.retry_after_seconds == 60


def test_security_configuration_failure_is_fail_closed(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    settings.session_secret_file.chmod(0o644)
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/auth/status")
        ready = client.get("/api/health/ready")

    assert status.status_code == 503
    assert status.json()["error"]["code"] == "security_unavailable"
    assert ready.status_code == 503
    assert ready.json()["checks"]["security"] is False


def test_session_secret_symlink_is_rejected(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    real_secret = settings.session_secret_file.with_name("real-session.key")
    settings.session_secret_file.rename(real_secret)
    settings.session_secret_file.symlink_to(real_secret)
    # Re-validate after the symlink exists: normalization must not hide the final link.
    reloaded_settings = Settings(_env_file=None, **settings.model_dump())
    with TestClient(create_app(reloaded_settings)) as client:
        status = client.get("/api/auth/status")

    assert status.status_code == 503
    assert status.json()["error"]["code"] == "security_unavailable"


def test_existing_password_verifier_symlink_is_not_hidden_by_configuration(
    tmp_path: Path,
) -> None:
    settings = build_settings(tmp_path)
    target = settings.password_verifier_file.with_name("target.argon2id")
    target.write_text("$argon2id$not-a-real-verifier\n", encoding="ascii")
    target.chmod(0o600)
    settings.password_verifier_file.symlink_to(target)
    reloaded_settings = Settings(_env_file=None, **settings.model_dump())

    with TestClient(create_app(reloaded_settings)) as client:
        status = client.get("/api/auth/status")

    assert status.status_code == 503
    assert status.json()["error"]["code"] == "security_unavailable"


def test_websocket_handshake_uses_same_session_and_origin_boundary(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        with (
            pytest.raises(WebSocketDisconnect) as unauthenticated,
            client.websocket_connect(
                "/ws/events",
                headers={"Origin": settings.canonical_public_origin},
            ),
        ):
            pass
        assert unauthenticated.value.code == 4401

        setup = _setup(client, settings)
        with (
            pytest.raises(WebSocketDisconnect) as cross_site,
            client.websocket_connect(
                "/ws/events",
                headers={"Origin": "http://attacker.example"},
            ),
        ):
            pass
        assert cross_site.value.code == 4403

        with client.websocket_connect(
            "/ws/events",
            headers={"Origin": settings.canonical_public_origin},
        ) as websocket:
            websocket.send_json(
                {
                    "type": "subscribe",
                    "csrfToken": setup["csrfToken"],
                    "lastSequence": 0,
                }
            )
            snapshot = websocket.receive_json()
            assert snapshot["type"] == "connection.snapshot"
            assert snapshot["snapshot"]["connection"]["state"] == "disconnected"
