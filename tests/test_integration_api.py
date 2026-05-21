import os
import uuid
import unittest

import requests


ENABLE_INTEGRATION = os.getenv("SANDBOX_TEST_ENABLE_INTEGRATION", "false").lower() == "true"
API_URL = os.getenv("SANDBOX_TEST_API_URL", "http://localhost:8088")
API_KEY = os.getenv("SANDBOX_TEST_API_KEY", "super-secret-agent-token-123")


@unittest.skipUnless(
    ENABLE_INTEGRATION,
    "Set SANDBOX_TEST_ENABLE_INTEGRATION=true to run live API integration tests."
)
class ApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = requests.Session()
        cls.session.headers.update({
            "X-API-Key": API_KEY,
            "Content-Type": "application/json",
        })

    def setUp(self):
        self.session_id = f"itest-{uuid.uuid4().hex[:10]}"

    def tearDown(self):
        self.session.delete(f"{API_URL}/api/v1/session/{self.session_id}", timeout=30)

    def test_workspace_file_lifecycle_and_execution(self):
        write_response = self.session.post(
            f"{API_URL}/api/v1/session/{self.session_id}/files/write",
            json={
                "path": "inputs/report.txt",
                "content": "AI Agent Intelligence Report Data",
                "encoding": "utf-8",
                "persist_workspace": True,
            },
            timeout=30,
        )
        self.assertEqual(write_response.status_code, 200, write_response.text)
        write_body = write_response.json()
        self.assertEqual(write_body["status"], "success")
        self.assertEqual(write_body["bytes_written"], 33)

        list_response = self.session.post(
            f"{API_URL}/api/v1/session/{self.session_id}/files/list",
            json={
                "path": ".",
                "recursive": True,
                "persist_workspace": True,
            },
            timeout=30,
        )
        self.assertEqual(list_response.status_code, 200, list_response.text)
        list_body = list_response.json()
        paths = {entry["path"] for entry in list_body["entries"]}
        self.assertIn("inputs/report.txt", paths)

        read_response = self.session.post(
            f"{API_URL}/api/v1/session/{self.session_id}/files/read",
            json={
                "path": "inputs/report.txt",
                "encoding": "utf-8",
                "persist_workspace": True,
            },
            timeout=30,
        )
        self.assertEqual(read_response.status_code, 200, read_response.text)
        read_body = read_response.json()
        self.assertEqual(read_body["content"], "AI Agent Intelligence Report Data")

        execute_response = self.session.post(
            f"{API_URL}/api/v1/execute",
            json={
                "type": "shell",
                "session_id": self.session_id,
                "persist_workspace": True,
                "code": "cat /workspace/inputs/report.txt",
            },
            timeout=30,
        )
        self.assertEqual(execute_response.status_code, 200, execute_response.text)
        execute_body = execute_response.json()
        self.assertEqual(execute_body["exit_code"], 0)
        self.assertEqual(execute_body["stdout"], "AI Agent Intelligence Report Data")

    def test_execution_runs_from_workspace_for_relative_output_files(self):
        execute_response = self.session.post(
            f"{API_URL}/api/v1/execute",
            json={
                "type": "python",
                "session_id": self.session_id,
                "persist_workspace": True,
                "code": (
                    "from pathlib import Path\n"
                    "Path('generated.txt').write_text('hello workspace', encoding='utf-8')\n"
                    "print(Path.cwd())"
                ),
            },
            timeout=30,
        )
        self.assertEqual(execute_response.status_code, 200, execute_response.text)
        execute_body = execute_response.json()
        self.assertEqual(execute_body["exit_code"], 0, execute_body)
        self.assertEqual(execute_body["stdout"].strip(), "/workspace")

        read_response = self.session.post(
            f"{API_URL}/api/v1/session/{self.session_id}/files/read",
            json={
                "path": "generated.txt",
                "encoding": "utf-8",
                "persist_workspace": True,
            },
            timeout=30,
        )
        self.assertEqual(read_response.status_code, 200, read_response.text)
        read_body = read_response.json()
        self.assertEqual(read_body["content"], "hello workspace")

    def test_purge_removes_persistent_workspace(self):
        write_response = self.session.post(
            f"{API_URL}/api/v1/session/{self.session_id}/files/write",
            json={
                "path": "tmp/marker.txt",
                "content": "marker",
                "encoding": "utf-8",
                "persist_workspace": True,
            },
            timeout=30,
        )
        self.assertEqual(write_response.status_code, 200, write_response.text)

        delete_response = self.session.delete(
            f"{API_URL}/api/v1/session/{self.session_id}",
            timeout=30,
        )
        self.assertEqual(delete_response.status_code, 200, delete_response.text)
        delete_body = delete_response.json()
        self.assertEqual(delete_body["status"], "success")

        list_response = self.session.post(
            f"{API_URL}/api/v1/session/{self.session_id}/files/list",
            json={
                "path": ".",
                "recursive": True,
                "persist_workspace": True,
            },
            timeout=30,
        )
        self.assertEqual(list_response.status_code, 200, list_response.text)
        list_body = list_response.json()
        self.assertEqual(list_body["entries"], [])


if __name__ == "__main__":
    unittest.main()
