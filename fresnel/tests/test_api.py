import pytest

from conftest import auth, brief_payload


def test_snapshot_etag_and_304(api):
    r = api.get("/v1/snapshot")
    assert r.status_code == 200 and r.json()["schema"] == 1
    assert r.json()["location"]["approx_lat"] == 39.1  # rounded
    etag = r.headers["etag"]
    assert api.get("/v1/snapshot", headers={"If-None-Match": etag}).status_code == 304


def test_initial_state_unknown(api):
    snap = api.get("/v1/snapshot").json()
    assert all(e["status"] == "unknown" for e in snap["sources"].values())
    assert snap["push"]["brief"]["status"] == "unknown"
    assert api.get("/v1/health").json()["degraded"] is True


def test_source_404(api):
    assert api.get("/v1/sources/nope").status_code == 404
    assert api.get("/v1/sources/swpc_kp").status_code == 200


def test_push_auth_matrix(api, tokens):
    url = "/v1/push/brief"
    assert api.put(url, json=brief_payload()).status_code == 401
    assert api.put(url, json=brief_payload(), headers=auth("wrong")).status_code == 401
    assert api.put(url, json=brief_payload(), headers=auth(tokens["context"])).status_code == 403
    assert api.put("/v1/push/nope", json={}, headers=auth(tokens["all"])).status_code == 404
    assert api.put(url, json=brief_payload(), headers=auth(tokens["brief"])).status_code == 200


def test_push_brief_roundtrip(api, tokens):
    r = api.put("/v1/push/brief", json=brief_payload(), headers=auth(tokens["all"]))
    assert r.status_code == 200
    got = api.get("/v1/brief").json()
    assert got["status"] == "info" and got["tier"] == "push"
    assert got["data"]["body"] == "All quiet."  # control char stripped
    assert api.get("/v1/brief/2026-10-14").json()["data"]["title"] == "Daily brief"
    assert api.get("/v1/brief/2020-01-01").status_code == 404
    assert api.get("/v1/brief/..%2Fx").status_code == 404


def test_push_scoped_token_can_write_its_slot(api, tokens):
    body = {"generated_at": "2026-10-14T11:00:00Z", "items": [{"title": "t", "url": "https://e.com"}]}
    assert api.put("/v1/push/context", json=body, headers=auth(tokens["context"])).status_code == 200


def test_push_bad_json_and_schema(api, tokens):
    assert api.put("/v1/push/brief", content=b"{nope", headers=auth(tokens["all"])).status_code == 400
    r = api.put("/v1/push/brief", json={"title": "x"}, headers=auth(tokens["all"]))
    assert r.status_code == 422 and r.json()["detail"]


def test_push_too_large(api, tokens):
    big = brief_payload(body="x" * 300_000)
    assert api.put("/v1/push/brief", json=big, headers=auth(tokens["all"])).status_code == 413


def test_push_disabled_without_tokens(config, relay):
    from fastapi.testclient import TestClient
    from fresnel.app import create_app
    with TestClient(create_app(config, relay=relay, start_background=False)) as c:
        assert c.put("/v1/push/brief", json=brief_payload()).status_code == 503


def test_stale_brief_goes_unknown(api, clock, tokens):
    api.put("/v1/push/brief", json=brief_payload(), headers=auth(tokens["all"]))
    clock.t = clock.t.replace(day=16)
    assert api.get("/v1/brief").json()["status"] == "unknown"


def test_serves_board_when_configured(config, relay, tmp_path):
    from fastapi.testclient import TestClient
    from fresnel.app import create_app
    board = tmp_path / "board"
    board.mkdir()
    (board / "index.html").write_text("<title>Lighthouse</title>")
    config.server.board_dir = str(board)
    with TestClient(create_app(config, relay=relay, start_background=False)) as c:
        r = c.get("/")
        assert r.status_code == 200 and "Lighthouse" in r.text
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert c.get("/v1/health").status_code == 200  # API still wins over the static mount


def test_board_dir_must_contain_index(config, relay, tmp_path):
    from fresnel.app import create_app
    config.server.board_dir = str(tmp_path)
    with pytest.raises(ValueError):
        create_app(config, relay=relay, start_background=False)


def test_long_brief_body_accepted(api, tokens):
    body = "line of text\n" * 4000  # about 52 KB, a long full briefing
    assert api.put("/v1/push/brief", json=brief_payload(body=body), headers=auth(tokens["all"])).status_code == 200


def test_cors_preflight_allows_etag_revalidation(config, relay):
    from fastapi.testclient import TestClient
    from fresnel.app import create_app
    config.server.cors_origins = ["https://board.example.org"]
    with TestClient(create_app(config, relay=relay, start_background=False)) as c:
        r = c.options("/v1/snapshot", headers={
            "Origin": "https://board.example.org",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "if-none-match",
        })
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "https://board.example.org"
        other = c.get("/v1/snapshot", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in other.headers
