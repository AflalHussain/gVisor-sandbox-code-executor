import json
import unittest
from unittest.mock import AsyncMock, patch

from sandbox_engine import SandboxEngine


class FakeProcess:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, input=None):
        self.input = input
        return self._stdout, self._stderr


class SandboxEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = SandboxEngine(image_name="sandbox-executor:test")

    def test_build_workspace_mount_ephemeral(self):
        self.assertEqual(self.engine._build_workspace_mount("session-1", False), ["-v", "/workspace"])

    def test_build_workspace_mount_persistent(self):
        mount = self.engine._build_workspace_mount("session-1", True)
        self.assertEqual(mount[0], "-v")
        self.assertTrue(mount[1].endswith(":/workspace"))
        self.assertIn(self.engine.volume_prefix, mount[1])

    def test_normalize_workspace_path_allows_root(self):
        self.assertEqual(self.engine._normalize_workspace_path("."), ".")
        self.assertEqual(self.engine._normalize_workspace_path(""), ".")

    def test_normalize_workspace_path_rejects_escape(self):
        with self.assertRaisesRegex(ValueError, "cannot escape"):
            self.engine._normalize_workspace_path("../secret.txt")

    async def test_run_code_async_surfaces_json_error_from_stdout(self):
        process = FakeProcess(
            returncode=1,
            stdout=json.dumps({"error": "executor failed"}).encode("utf-8"),
            stderr=b""
        )

        with patch("sandbox_engine.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            result = await self.engine.run_code_async("print('hi')")

        self.assertEqual(result, {"error": "executor failed"})

    async def test_write_file_async_validates_and_delegates(self):
        self.engine._run_workspace_helper_async = AsyncMock(return_value={"status": "success"})

        result = await self.engine.write_file_async(
            session_id="session-1",
            path="uploads/input.txt",
            content="hello world",
            encoding="utf-8",
            persist_workspace=True,
            overwrite=False
        )

        self.assertEqual(result, {"status": "success"})
        self.engine._run_workspace_helper_async.assert_awaited_once()
        kwargs = self.engine._run_workspace_helper_async.await_args.kwargs
        self.assertEqual(kwargs["session_id"], "session-1")
        self.assertTrue(kwargs["persist_workspace"])
        self.assertEqual(kwargs["payload"]["path"], "uploads/input.txt")
        self.assertFalse(kwargs["payload"]["overwrite"])

    async def test_list_files_async_supports_root_path(self):
        self.engine._run_workspace_helper_async = AsyncMock(return_value={"status": "success", "entries": []})

        result = await self.engine.list_files_async(
            session_id="session-1",
            path=".",
            persist_workspace=True,
            recursive=False
        )

        self.assertEqual(result["status"], "success")
        kwargs = self.engine._run_workspace_helper_async.await_args.kwargs
        self.assertEqual(kwargs["payload"]["path"], ".")
        self.assertFalse(kwargs["payload"]["recursive"])

    async def test_purge_workspace_async_removes_session_volume(self):
        process = FakeProcess(returncode=0, stdout=b"deleted", stderr=b"")

        with patch("sandbox_engine.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as create_proc:
            result = await self.engine.purge_workspace_async("session-1")

        self.assertEqual(result["status"], "success")
        self.assertIn(self.engine.volume_prefix, result["volume"])
        cmd = create_proc.await_args.args
        self.assertEqual(cmd[:4], ("docker", "volume", "rm", "-f"))


if __name__ == "__main__":
    unittest.main()
