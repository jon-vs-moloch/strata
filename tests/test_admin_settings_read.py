"""GET /admin/settings is a read: it must not create rows or commit."""

from fastapi.testclient import TestClient

from strata.api import main as api_main


class _ReadOnlyParameters:
    def peek_parameter(self, key, default_value=None):
        return default_value

    def get_parameter(self, *args, **kwargs):
        raise AssertionError("GET /admin/settings must not call get_parameter (it creates rows)")


class _RecordingStorage:
    def __init__(self):
        self.parameters = _ReadOnlyParameters()
        self.commits = 0

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def test_get_settings_does_not_commit():
    storage = _RecordingStorage()
    api_main.app.dependency_overrides[api_main.get_storage] = lambda: storage
    try:
        client = TestClient(api_main.app, base_url="http://127.0.0.1:8000")
        response = client.get("/admin/settings", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert storage.commits == 0
    finally:
        api_main.app.dependency_overrides.pop(api_main.get_storage, None)
