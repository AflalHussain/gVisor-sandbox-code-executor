import unittest
from unittest.mock import AsyncMock

try:
    from fastapi.testclient import TestClient
    import api_gateway as gateway
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"API test dependencies unavailable: {exc}")


class ApiGatewayTests(unittest.TestCase):
    def setUp(self):
        self.original_engine = gateway.engine

        class FakeEngine:
            default_persist_workspace = False

            def __init__(self):
                self.run_code_async = AsyncMock(return_value={"stdout": "ok", "stderr": "", "exit_code": 0})
                self.write_file_async = AsyncMock(return_value={"status": "success", "bytes_written": 5})
                self.read_file_async = AsyncMock(return_value={"status": "success", "content": "hello", "size": 5})
                self.list_files_async = AsyncMock(return_value={"status": "success", "entries": [{"path": "a.txt", "type": "file", "size": 5}]})
                self.purge_workspace_async = AsyncMock(return_value={"status": "success", "volume": "sandbox-session-123", "details": "deleted"})

        gateway.engine = FakeEngine()
        self.client = TestClient(gateway.app)

    def tearDown(self):
        gateway.engine = self.original_engine

    def test_execute_endpoint_returns_session_and_persistence(self):
        response = self.client.post(
            "/api/v1/execute",
            headers={"X-API-Key": gateway.API_KEY},
            json={
                "type": "shell",
                "session_id": "session-1",
                "persist_workspace": True,
                "code": "echo hello"
            }
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["session_id"], "session-1")
        self.assertTrue(body["persist_workspace"])
        gateway.engine.run_code_async.assert_awaited_once_with(
            code="echo hello",
            run_type="shell",
            allowed_pip=[],
            session_id="session-1",
            persist_workspace=True
        )

    def test_write_file_endpoint(self):
        response = self.client.post(
            "/api/v1/session/session-1/files/write",
            headers={"X-API-Key": gateway.API_KEY},
            json={
                "path": "uploads/input.txt",
                "content": "hello",
                "encoding": "utf-8",
                "persist_workspace": True
            }
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["session_id"], "session-1")
        self.assertTrue(body["persist_workspace"])
        gateway.engine.write_file_async.assert_awaited_once_with(
            session_id="session-1",
            path="uploads/input.txt",
            content="hello",
            encoding="utf-8",
            persist_workspace=True,
            overwrite=True
        )

    def test_list_files_endpoint(self):
        response = self.client.post(
            "/api/v1/session/session-1/files/list",
            headers={"X-API-Key": gateway.API_KEY},
            json={
                "path": ".",
                "recursive": False,
                "persist_workspace": True
            }
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["entries"][0]["path"], "a.txt")
        gateway.engine.list_files_async.assert_awaited_once_with(
            session_id="session-1",
            path=".",
            recursive=False,
            persist_workspace=True
        )

    def test_delete_session_endpoint(self):
        response = self.client.delete(
            "/api/v1/session/session-1",
            headers={"X-API-Key": gateway.API_KEY}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "success")
        gateway.engine.purge_workspace_async.assert_awaited_once_with("session-1")


if __name__ == "__main__":
    unittest.main()
