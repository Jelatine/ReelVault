from types import SimpleNamespace

from reelvault import auth, totp
from reelvault.auth_keys import KEY_FILE
from reelvault.models import TwoFactor

from .conftest import PASSWORD, USER


def test_enrollment_login_replay_recovery_and_disable(client, settings, monkeypatch):
    now = [1800000000]
    monkeypatch.setattr(totp, "time", SimpleNamespace(time=lambda: now[0]))
    first_cookie = client.cookies.get(auth.COOKIE_NAME)
    credentials = {"username": USER, "password": PASSWORD}
    assert client.post("/api/auth/login", json=credentials).status_code == 200
    old_cookie = client.cookies.get(auth.COOKIE_NAME)
    client.cookies.set(auth.COOKIE_NAME, first_cookie)
    assert client.get("/api/auth/totp").json()["enabled"] is False
    body = {"current_password": PASSWORD}
    enrollment = client.post("/api/auth/totp/enroll", json=body)
    assert enrollment.status_code == 200
    assert enrollment.headers["cache-control"] == "no-store"
    setup = enrollment.json()
    assert "otpauth://totp/" in setup["uri"] and "<svg" in setup["qr_svg"]
    assert client.get("/api/auth/totp").json()["enabled"] is False
    code = totp.hotp(setup["secret"], now[0] // 30)
    confirmed = client.post("/api/auth/totp/confirm", json={**body, "code": code})
    assert confirmed.status_code == 200, confirmed.text
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == 10
    client.cookies.set(auth.COOKIE_NAME, old_cookie)
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set(auth.COOKIE_NAME, first_cookie)
    assert client.get("/api/auth/totp").json()["enabled"] is True
    with client.app.state.sessionmaker() as db:
        record = db.get(TwoFactor, 1)
        assert setup["secret"] not in record.secret_ciphertext
        assert all(c.replace("-", "") not in record.recovery_hashes for c in codes)
    client.cookies.clear()
    required = client.post("/api/auth/login", json=credentials)
    assert required.status_code == 403 and required.json()["code"] == "totp_required"
    assert not required.headers.get("set-cookie")
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={**credentials, "code": code}).status_code == 403
    now[0] += 30
    code = totp.hotp(setup["secret"], now[0] // 30)
    assert client.post("/api/auth/login", json={**credentials, "code": code}).status_code == 200
    client.cookies.clear()
    assert client.post("/api/auth/login", json={**credentials, "code": code}).status_code == 403
    (settings.data_dir / KEY_FILE).unlink()
    recovered = client.post("/api/auth/login", json={**credentials, "code": codes[0]})
    assert recovered.status_code == 200
    assert client.get("/api/auth/totp").json()["recovery_codes_remaining"] == 9
    assert client.post("/api/auth/totp/disable", json=body).status_code == 403
    assert client.post("/api/auth/totp/disable", json={**body, "code": codes[0]}).status_code == 403
    disabled = client.post("/api/auth/totp/disable", json={**body, "code": codes[1]})
    assert disabled.status_code == 200
    client.cookies.clear()
    assert client.post("/api/auth/login", json=credentials).status_code == 200


def test_pending_enrollment_bound_to_session_and_expiry(client):
    from datetime import timedelta

    from reelvault.models import utcnow

    body = {"current_password": PASSWORD}
    wrong = client.post("/api/auth/totp/enroll", json={"current_password": "wrong"})
    assert wrong.status_code == 400
    setup = client.post("/api/auth/totp/enroll", json=body).json()
    code = totp.hotp(setup["secret"], int(totp.time.time() // 30))
    client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
    result = client.post("/api/auth/totp/confirm", json={**body, "code": code})
    assert result.status_code == 409
    assert client.get("/api/auth/totp").json()["enabled"] is False
    setup = client.post("/api/auth/totp/enroll", json=body).json()
    with client.app.state.sessionmaker() as db:
        record = db.get(TwoFactor, 1)
        record.pending_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post("/api/auth/totp/confirm", json={**body, "code": code}).status_code == 409
    assert client.post("/api/auth/totp/disable", json=body).status_code == 200


def test_simultaneous_login_cannot_reuse_totp_or_recovery_code(client, settings, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    from reelvault.auth_keys import encrypt_secret
    from reelvault.models import utcnow
    from reelvault.two_factor import new_recovery_codes

    from .conftest import HEADERS

    monkeypatch.setattr(totp, "time", SimpleNamespace(time=lambda: 1800000000))
    secret = totp.new_secret()
    with client.app.state.sessionmaker() as db:
        record = TwoFactor(
            user_id=1,
            secret_ciphertext=encrypt_secret(settings.data_dir, secret, create=True),
            enabled_at=utcnow(),
            last_counter=-1,
        )
        recovery = new_recovery_codes(record)[0]
        db.add(record)
        db.commit()

    def login(code):
        visitor = TestClient(client.app, headers=HEADERS)
        try:
            return visitor.post(
                "/api/auth/login",
                json={
                    "username": USER,
                    "password": PASSWORD,
                    "code": code,
                },
            ).status_code
        finally:
            visitor.close()

    for code in (totp.hotp(secret, 1800000000 // 30), recovery):
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(login, [code, code])) == [200, 403]


def test_recovery_rotation_password_change_and_peer_rate_limit(client, monkeypatch):
    monkeypatch.setattr(totp, "time", SimpleNamespace(time=lambda: 1800000000))
    body = {"current_password": PASSWORD}
    secret = client.post("/api/auth/totp/enroll", json=body).json()["secret"]
    codes = client.post(
        "/api/auth/totp/confirm",
        json={
            **body,
            "code": totp.hotp(secret, 1800000000 // 30),
        },
    ).json()["recovery_codes"]
    assert (
        client.post("/api/auth/password", json={**body, "new_password": "new-password"}).status_code
        == 403
    )
    rotated = client.post("/api/auth/totp/recovery-codes", json={**body, "code": codes[0]})
    assert rotated.status_code == 200
    new_codes = rotated.json()["recovery_codes"]
    assert new_codes != codes
    assert client.post("/api/auth/totp/disable", json={**body, "code": codes[1]}).status_code == 403
    changed = client.post(
        "/api/auth/password",
        json={
            **body,
            "new_password": "new-password",
            "code": new_codes[0],
        },
    )
    assert changed.status_code == 200
    credentials = {"username": USER, "password": "new-password", "code": "invalid"}
    for index in range(4):
        result = client.post(
            "/api/auth/login", json=credentials, headers={"X-Forwarded-For": f"192.0.2.{index}"}
        )
        assert result.status_code == 403
    blocked = client.post(
        "/api/auth/login", json=credentials, headers={"X-Forwarded-For": "192.0.2.100"}
    )
    assert blocked.status_code == 429
