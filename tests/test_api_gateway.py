import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException

import api_gateway as gateway
from shared_models import CodeExecutionRequest, WorkspaceListRequest, WorkspaceWriteRequest
from worker_client import WorkerServiceError


class ApiGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_worker_client = gateway.worker_client

        class FakeWorkerClient:
            def __init__(self):
                self.run_code_async = AsyncMock(return_value={"stdout": "ok", "stderr": "", "exit_code": 0})
                self.write_file_async = AsyncMock(return_value={"status": "success", "bytes_written": 5})
                self.read_file_async = AsyncMock(return_value={"status": "success", "content": "hello", "size": 5})
                self.list_files_async = AsyncMock(return_value={"status": "success", "entries": [{"path": "a.txt", "type": "file", "size": 5}]})
                self.purge_workspace_async = AsyncMock(return_value={"status": "success", "volume": "sandbox-session-123", "details": "deleted"})

        gateway.worker_client = FakeWorkerClient()

    def tearDown(self):
        gateway.worker_client = self.original_worker_client

    async def test_execute_endpoint_returns_session_and_persistence(self):
        result = await gateway.execute_untrusted_payload(
            CodeExecutionRequest(
                type="shell",
                session_id="session-1",
                persist_workspace=True,
                code="echo hello",
            )
        )

        self.assertEqual(result["session_id"], "session-1")
        self.assertTrue(result["persist_workspace"])
        gateway.worker_client.run_code_async.assert_awaited_once_with(
            code="echo hello",
            run_type="shell",
            allowed_pip=[],
            session_id="session-1",
            persist_workspace=True,
        )

    async def test_write_file_endpoint(self):
        result = await gateway.write_session_file(
            "session-1",
            WorkspaceWriteRequest(
                path="uploads/input.txt",
                content="hello",
                encoding="utf-8",
                persist_workspace=True,
            ),
        )

        self.assertEqual(result["session_id"], "session-1")
        self.assertTrue(result["persist_workspace"])
        gateway.worker_client.write_file_async.assert_awaited_once_with(
            session_id="session-1",
            path="uploads/input.txt",
            content="hello",
            encoding="utf-8",
            persist_workspace=True,
            overwrite=True,
        )

    async def test_list_files_endpoint(self):
        result = await gateway.list_session_files(
            "session-1",
            WorkspaceListRequest(
                path=".",
                recursive=False,
                persist_workspace=True,
            ),
        )

        self.assertEqual(result["entries"][0]["path"], "a.txt")
        gateway.worker_client.list_files_async.assert_awaited_once_with(
            session_id="session-1",
            path=".",
            recursive=False,
            persist_workspace=True,
        )

    async def test_delete_session_endpoint(self):
        result = await gateway.close_and_purge_session("session-1")

        self.assertEqual(result["status"], "success")
        gateway.worker_client.purge_workspace_async.assert_awaited_once_with("session-1")

    async def test_worker_error_maps_to_http_exception(self):
        gateway.worker_client.read_file_async = AsyncMock(side_effect=WorkerServiceError(400, "bad path"))

        with self.assertRaises(HTTPException) as ctx:
            await gateway.read_session_file(
                "session-1",
                gateway.WorkspaceReadRequest(path="../oops", encoding="utf-8", persist_workspace=True),
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "bad path")


if __name__ == "__main__":
    unittest.main()
