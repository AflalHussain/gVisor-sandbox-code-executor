import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException

import worker_service
from shared_models import CodeExecutionRequest, WorkspaceWriteRequest


class WorkerServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_engine = worker_service.engine

        class FakeEngine:
            def __init__(self):
                self.run_code_async = AsyncMock(return_value={"stdout": "ok", "stderr": "", "exit_code": 0})
                self.write_file_async = AsyncMock(return_value={"status": "success", "bytes_written": 5})
                self.read_file_async = AsyncMock(return_value={"status": "success", "content": "hello", "size": 5})
                self.list_files_async = AsyncMock(return_value={"status": "success", "entries": [{"path": "a.txt", "type": "file", "size": 5}]})
                self.purge_workspace_async = AsyncMock(return_value={"status": "success", "volume": "sandbox-session-123"})

        worker_service.engine = FakeEngine()

    def tearDown(self):
        worker_service.engine = self.original_engine

    async def test_healthcheck_requires_auth(self):
        with self.assertRaises(HTTPException) as ctx:
            await worker_service.verify_worker_auth("")

        self.assertEqual(ctx.exception.status_code, 403)

    async def test_execute_endpoint_delegates_to_engine(self):
        result = await worker_service.execute_untrusted_payload(
            CodeExecutionRequest(
                code="print('hello')",
                type="python",
                session_id="session-1",
                persist_workspace=True,
            )
        )

        self.assertEqual(result["exit_code"], 0)
        worker_service.engine.run_code_async.assert_awaited_once_with(
            code="print('hello')",
            run_type="python",
            allowed_pip=[],
            session_id="session-1",
            persist_workspace=True,
        )

    async def test_write_file_validation_maps_to_400(self):
        worker_service.engine.write_file_async = AsyncMock(side_effect=ValueError("bad path"))

        with self.assertRaises(HTTPException) as ctx:
            await worker_service.write_session_file(
                "session-1",
                WorkspaceWriteRequest(
                    path="../oops",
                    content="hello",
                    encoding="utf-8",
                ),
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "bad path")

    async def test_purge_endpoint_passthrough(self):
        result = await worker_service.close_and_purge_session("session-1")

        self.assertEqual(result["status"], "success")
        worker_service.engine.purge_workspace_async.assert_awaited_once_with("session-1")


if __name__ == "__main__":
    unittest.main()
