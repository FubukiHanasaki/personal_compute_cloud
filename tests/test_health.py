from fastapi.testclient import TestClient
from pcc_server.app import create_app
from pcc_server.config import Settings


def test_health_and_errors(tmp_path):
    app = create_app(Settings(secret="x"*40, environment="development", database_url=f"sqlite:///{tmp_path}/db"))
    with TestClient(app) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        response = client.get("/missing")
        assert response.status_code == 404
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
