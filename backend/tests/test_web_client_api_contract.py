from pathlib import Path


SERVER = Path(__file__).resolve().parents[1] / "main.py"


def test_server_exposes_provider_neutral_authorization_api():
    server = SERVER.read_text()

    assert '@app.post("/api/auth/start")' in server
    assert '@app.get("/api/auth/poll/{state}")' in server


def test_server_keeps_legacy_plex_routes_behind_generic_authorization_capability():
    server = SERVER.read_text()

    assert '@app.post("/api/auth/plex/start", include_in_schema=False)' in server
    assert '@app.get("/api/auth/plex/poll/{state}", include_in_schema=False)' in server
