import os

import httpx


class WorkerServiceError(Exception):
    def __init__(self, status_code: int, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class SandboxWorkerClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or os.getenv("SANDBOX_WORKER_URL", "http://sandbox-worker:8081")).rstrip("/")
        self.api_key = api_key or os.getenv("SANDBOX_WORKER_API_KEY", "change-me-worker-secret")
        self.timeout = timeout if timeout is not None else float(os.getenv("SANDBOX_WORKER_TIMEOUT_SECONDS", "90"))

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Worker-Auth": self.api_key}

    async def _request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=json_body,
                    headers=self._headers,
                )
        except httpx.HTTPError as exc:
            raise WorkerServiceError(502, f"Sandbox worker unavailable: {exc}") from exc

        if response.is_success:
            return response.json()

        detail = response.text
        try:
            payload = response.json()
            detail = payload.get("detail", payload)
        except ValueError:
            pass
        raise WorkerServiceError(response.status_code, detail)

    async def healthcheck(self) -> dict:
        return await self._request("GET", "/internal/v1/health")

    async def run_code_async(
        self,
        code: str,
        run_type: str = "python",
        allowed_pip: list | None = None,
        session_id: str = "default",
        persist_workspace: bool | None = None,
    ) -> dict:
        return await self._request(
            "POST",
            "/internal/v1/execute",
            {
                "code": code,
                "type": run_type,
                "install_packages": allowed_pip or [],
                "session_id": session_id,
                "persist_workspace": persist_workspace,
            },
        )

    async def write_file_async(
        self,
        session_id: str,
        path: str,
        content: str,
        encoding: str = "utf-8",
        persist_workspace: bool | None = None,
        overwrite: bool = True,
    ) -> dict:
        return await self._request(
            "POST",
            f"/internal/v1/session/{session_id}/files/write",
            {
                "path": path,
                "content": content,
                "encoding": encoding,
                "persist_workspace": persist_workspace,
                "overwrite": overwrite,
            },
        )

    async def read_file_async(
        self,
        session_id: str,
        path: str,
        encoding: str = "base64",
        persist_workspace: bool | None = None,
    ) -> dict:
        return await self._request(
            "POST",
            f"/internal/v1/session/{session_id}/files/read",
            {
                "path": path,
                "encoding": encoding,
                "persist_workspace": persist_workspace,
            },
        )

    async def list_files_async(
        self,
        session_id: str,
        path: str = ".",
        persist_workspace: bool | None = None,
        recursive: bool = True,
    ) -> dict:
        return await self._request(
            "POST",
            f"/internal/v1/session/{session_id}/files/list",
            {
                "path": path,
                "recursive": recursive,
                "persist_workspace": persist_workspace,
            },
        )

    async def purge_workspace_async(self, session_id: str) -> dict:
        return await self._request("DELETE", f"/internal/v1/session/{session_id}")
