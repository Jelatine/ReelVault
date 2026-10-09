from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from reelvault.errors import STATUS_CODES, APIError, install_error_handlers


def test_auth_and_resource_errors_have_semantic_codes(anon, client):
    response = client.get("/api/videos/" + "f" * 32)
    assert response.status_code == 404
    assert response.json() == {"detail": "视频不存在", "code": "video_not_found", "params": {}}
    response = client.post("/api/folders", json={"name": "same"})
    assert response.status_code == 200
    response = client.post("/api/folders", json={"name": "same"})
    assert response.status_code == 409
    assert response.json()["code"] == "folder_name_conflict"
    client.post("/api/auth/logout")
    response = anon.get("/api/videos")
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"
    response = anon.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_offset_validation_csrf_and_rate_limit_remain_machine_readable(client):
    response = client.post("/api/uploads", json={"filename": "a.mp4", "size": 10})
    upload = response.json()["id"]
    response = client.put(f"/api/uploads/{upload}?offset=5", content=b"bytes")
    assert response.status_code == 409
    assert response.json() == {
        "detail": {"message": "偏移量不匹配", "received": 0},
        "code": "upload_offset_mismatch",
        "params": {"received": 0},
    }
    response = client.post("/api/uploads", json={"filename": "a.mp4", "size": -1})
    assert response.status_code == 422
    data = response.json()
    assert data["code"] == "validation_error"
    assert isinstance(data["detail"], list)
    assert any(item["loc"] == ["body", "size"] for item in data["params"]["errors"])
    # Remove the fixture's default header entirely to exercise middleware rejection.
    request = client.build_request("POST", "/api/folders", json={"name": "x"})
    del request.headers["X-Requested-With"]
    response = client.send(request)
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_header_missing"
    client.post("/api/auth/logout")
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert response.status_code == 429
    assert response.json()["code"] == "login_rate_limited"
    assert response.json()["params"]["minutes"] >= 1


def test_framework_errors_headers_and_unexpected_failures():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/protected")
    def protected():
        raise APIError(
            401, "expired", code="authentication_required", headers={"WWW-Authenticate": "Bearer"}
        )

    @app.get("/temporary")
    def temporary():
        raise HTTPException(503, "temporarily offline", headers={"Retry-After": "30"})

    @app.get("/broken")
    def broken():
        raise RuntimeError("private internal diagnostic")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/protected")
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["code"] == "authentication_required"
        response = client.get("/temporary")
        assert response.headers["retry-after"] == "30"
        assert response.json()["code"] == "service_unavailable"
        assert client.get("/missing").json()["code"] == "not_found"
        assert client.post("/protected").json()["code"] == "method_not_allowed"
        response = client.get("/broken")
        assert response.status_code == 500
        assert response.json()["code"] == "internal_error"
        assert "private internal diagnostic" not in response.text


def test_all_application_errors_have_explicit_localizable_codes():
    backend = Path(__file__).parents[1]
    catalog_path = backend.parent / "frontend/src/locales/api-errors.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    codes = set(STATUS_CODES.values()) | {"request_failed", "csrf_header_missing"}
    count = 0
    for path in [*sorted((backend / "reelvault/api").glob("*.py")), backend / "reelvault/auth.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            assert node.func.id != "HTTPException", (
                f"Uncoded application error in {path}:{node.lineno}"
            )
            if node.func.id != "APIError":
                continue
            code = next(keyword.value for keyword in node.keywords if keyword.arg == "code")
            assert isinstance(code, ast.Constant) and isinstance(code.value, str)
            assert re.fullmatch(r"[a-z][a-z0-9_]+", code.value)
            codes.add(code.value)
            count += 1
    assert count >= 137
    assert codes <= catalog.keys()
    for code in codes:
        assert catalog[code]["en"] and catalog[code]["zh"]
        assert not re.search(r"[\u4e00-\u9fff]", catalog[code]["en"])
        assert set(re.findall(r"{{(.*?)}}", catalog[code]["en"])) == set(
            re.findall(r"{{(.*?)}}", catalog[code]["zh"])
        )
