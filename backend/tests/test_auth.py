from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from reelvault.auth import COOKIE_NAME
from reelvault.config import Settings
from reelvault.main import create_app
from reelvault.models import AuthSession

from .conftest import HEADERS, PASSWORD, USER, login


def test_requires_login(anon: TestClient) -> None:
    assert anon.get("/api/videos").status_code == 401
    assert anon.get("/api/auth/status").json() == {
        "setup_required": False,
        "authenticated": False,
    }


def test_login_wrong_password_and_lockout(anon: TestClient) -> None:
    for _ in range(3):
        r = anon.post("/api/auth/login", json={"username": USER, "password": "nope"})
        assert r.status_code == 401
    r = anon.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
    assert r.status_code == 429


def test_csrf_header_required(anon: TestClient) -> None:
    raw = TestClient(anon.app)
    r = raw.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
    assert r.status_code == 403


def test_remember_me_sets_persistent_cookie(anon: TestClient) -> None:
    r = anon.post(
        "/api/auth/login",
        json={"username": USER, "password": PASSWORD, "remember": True, "device_name": "phone"},
    )
    cookie = r.headers["set-cookie"]
    assert "Max-Age=" in cookie and "HttpOnly" in cookie
    r = anon.post(
        "/api/auth/login", json={"username": USER, "password": PASSWORD, "remember": False}
    )
    assert "Max-Age=" not in r.headers["set-cookie"]


def test_multi_device_sessions_and_revoke(settings: Settings) -> None:
    app = create_app(settings)
    with (
        TestClient(app, headers=HEADERS) as laptop,
        TestClient(app, headers=HEADERS) as phone,
    ):
        login(laptop, device="laptop")
        login(phone, remember=True, device="phone")
        assert laptop.get("/api/auth/me").status_code == 200
        assert phone.get("/api/auth/me").status_code == 200

        sessions = laptop.get("/api/auth/sessions").json()
        assert {s["device_name"] for s in sessions} == {"laptop", "phone"}
        phone_id = next(s["id"] for s in sessions if s["device_name"] == "phone")
        assert next(s for s in sessions if s["current"])["device_name"] == "laptop"

        assert laptop.delete(f"/api/auth/sessions/{phone_id}").status_code == 200
        assert phone.get("/api/auth/me").status_code == 401
        assert laptop.get("/api/auth/me").status_code == 200

        login(phone, device="phone2")
        assert laptop.post("/api/auth/sessions/revoke-others").json() == {"revoked": 1}
        assert phone.get("/api/auth/me").status_code == 401


def test_token_rotation_keeps_session(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app, headers=HEADERS) as c:
        login(c, remember=True)
        old = c.cookies.get(COOKIE_NAME)
        with app.state.sessionmaker() as db:
            sess = db.query(AuthSession).one()
            sess.rotated_at -= timedelta(days=settings.token_rotate_days + 1)
            db.commit()
        r = c.get("/api/auth/me")
        assert r.status_code == 200
        new = c.cookies.get(COOKIE_NAME)
        assert new and new != old
        assert c.get("/api/videos").status_code == 200
        # the old token still works during the grace period
        stale = TestClient(app, headers=HEADERS, cookies={COOKIE_NAME: old or ""})
        assert stale.get("/api/auth/me").status_code == 200


def test_change_password_logs_out_others(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app, headers=HEADERS) as a, TestClient(app, headers=HEADERS) as b:
        login(a)
        login(b)
        r = a.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": "newpass99"},
        )
        assert r.json()["revoked"] == 1
        assert b.get("/api/auth/me").status_code == 401
        assert a.get("/api/auth/me").status_code == 200
        r = a.post("/api/auth/login", json={"username": USER, "password": "newpass99"})
        assert r.status_code == 200


def test_setup_flow(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(data_dir=tmp_path / "d", static_dir=tmp_path / "none")
    with TestClient(create_app(settings), headers=HEADERS) as c:
        assert c.get("/api/auth/status").json()["setup_required"] is True
        r = c.post("/api/auth/setup", json={"username": "boss", "password": "hunter22"})
        assert r.status_code == 200
        assert c.get("/api/auth/me").json()["username"] == "boss"
        r = c.post("/api/auth/setup", json={"username": "x", "password": "hunter22"})
        assert r.status_code == 409


def test_logout(client: TestClient) -> None:
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
