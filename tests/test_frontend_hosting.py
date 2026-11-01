"""The container serves the built UI and API from the same origin."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from itrtg_web.frontend import mount_frontend


@pytest.fixture
def api_app():
    app = FastAPI()

    @app.get("/api/engines")
    def engines():
        return [{"id": "test-engine"}]

    return app


@pytest.fixture
def deployed_client(tmp_path, api_app):
    dist = tmp_path / "frontend" / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>Optimizer</title><main>Planner</main>", encoding="utf-8"
    )
    (assets / "app.js").write_text("console.log('optimizer asset');", encoding="utf-8")
    mount_frontend(api_app, dist)
    with TestClient(api_app) as client:
        yield client


def test_deployment_serves_the_ui_and_assets_without_shadowing_api_routes(deployed_client):
    index = deployed_client.get("/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert "<main>Planner</main>" in index.text

    asset = deployed_client.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.text == "console.log('optimizer asset');"

    engines = deployed_client.get("/api/engines")
    assert engines.status_code == 200
    assert engines.json() == [{"id": "test-engine"}]
    missing = deployed_client.get("/api/not-an-endpoint")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Not Found"}


def test_development_api_still_works_without_a_frontend_build(tmp_path, api_app):
    missing = tmp_path / "frontend" / "dist"
    mount_frontend(api_app, missing)

    with TestClient(api_app) as client:
        assert client.get("/api/engines").json() == [{"id": "test-engine"}]
        assert client.get("/").status_code == 404
    assert not missing.exists()
