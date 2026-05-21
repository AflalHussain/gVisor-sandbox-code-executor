import asyncio
import base64
import hashlib
import json
import os
from pathlib import PurePosixPath

class SandboxEngine:
    def __init__(self, image_name="sandbox-executor:latest"):
        self.image_name = image_name
        self.default_persist_workspace = os.getenv("SANDBOX_PERSISTENT_WORKSPACE_DEFAULT", "false").lower() == "true"
        self.volume_prefix = os.getenv("SANDBOX_VOLUME_PREFIX", "sandbox-session")

    def _build_workspace_mount(self, session_id: str, persist_workspace: bool) -> list[str]:
        if not persist_workspace:
            return ["-v", "/workspace"]

        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
        volume_name = f"{self.volume_prefix}-{digest}"
        return ["-v", f"{volume_name}:/workspace"]

    def _normalize_workspace_path(self, path: str) -> str:
        candidate = PurePosixPath(path)
        if candidate.is_absolute():
            raise ValueError("Workspace paths must be relative to /workspace.")

        parts = [part for part in candidate.parts if part not in ("", ".")]
        if not parts:
            return "."
        if any(part == ".." for part in parts):
            raise ValueError("Workspace paths cannot escape the session workspace.")

        return PurePosixPath(*parts).as_posix()

    async def _run_workspace_helper_async(
        self,
        session_id: str,
        persist_workspace: bool,
        helper_code: str,
        payload: dict,
    ) -> dict:
        cmd = [
            "docker", "run",
            "--interactive",
            "--runtime=runsc",
            "--network=none",
            "--memory=256m",
            "--rm",
            *self._build_workspace_mount(session_id, persist_workspace),
            self.image_name,
            "python3", "-c", helper_code
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout_bytes, stderr_bytes = await process.communicate(
            input=json.dumps(payload).encode("utf-8")
        )
        stdout_text = stdout_bytes.decode("utf-8").strip()
        stderr_text = stderr_bytes.decode("utf-8").strip()

        if process.returncode != 0:
            if stdout_text:
                try:
                    return json.loads(stdout_text)
                except json.JSONDecodeError:
                    pass
            return {"error": "Workspace helper failed", "details": stderr_text}

        return json.loads(stdout_text) if stdout_text else {"status": "success"}

    async def run_code_async(
        self,
        code: str,
        run_type: str = "python",
        allowed_pip: list | None = None,
        session_id: str = "default",
        persist_workspace: bool | None = None,
    ) -> dict:
        """Asynchronously executes untrusted code inside an isolated container workspace."""
        if allowed_pip is None:
            allowed_pip = []
        if persist_workspace is None:
            persist_workspace = self.default_persist_workspace

        # Build the final production-ready execution flags array
        cmd = [
            "docker", "run",
            "--interactive",
            "--runtime=runsc",           # Route execution natively inside gVisor
            "--network=bridge",
            "--dns=8.8.8.8",             # Evade local Ubuntu loopback dns restrictions
            "--memory=256m",             # Strict physical memory limit boundaries
            #"--pids-limit=20",           # Defends host process tracking spaces from forks
            "--rm",                      # Automatically flushes everything cleanly on execution stop
            *self._build_workspace_mount(session_id, persist_workspace),
            self.image_name,
            "python3", "/usr/local/bin/executor.py"
        ]

        payload = {
            "code": code,
            "type": run_type,
            "install_packages": allowed_pip
        }
        payload_bytes = json.dumps(payload).encode('utf-8')

        try:
            # Dispatch command invocation loop asynchronously
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Stream payload blocks directly down the stdin channel pipeline
            stdout_bytes, stderr_bytes = await process.communicate(input=payload_bytes)
            stdout_text = stdout_bytes.decode('utf-8').strip()
            stderr_text = stderr_bytes.decode('utf-8').strip()

            if process.returncode != 0:
                # If the executor emitted structured JSON, surface that instead of
                # misreporting it as a container runtime failure.
                if stdout_text:
                    try:
                        return json.loads(stdout_text)
                    except json.JSONDecodeError:
                        pass

                return {"error": "Container runtime crash inside gVisor", "details": stderr_text}

            # Return structural json telemetry outputs safely back to our application router
            return json.loads(stdout_text)

        except Exception as e:
            return {"error": f"Orchestration interface runtime failure: {str(e)}"}

    async def write_file_async(
        self,
        session_id: str,
        path: str,
        content: str,
        encoding: str = "utf-8",
        persist_workspace: bool | None = None,
        overwrite: bool = True,
    ) -> dict:
        if persist_workspace is None:
            persist_workspace = self.default_persist_workspace

        normalized_path = self._normalize_workspace_path(path)
        if encoding not in {"utf-8", "base64"}:
            raise ValueError("encoding must be 'utf-8' or 'base64'.")
        if encoding == "base64":
            # Validate before dispatching work into the helper container.
            base64.b64decode(content, validate=True)

        helper_code = """
import base64
import json
import os
from pathlib import Path

payload = json.loads(input())
target = Path("/workspace") / payload["path"]
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists() and not payload["overwrite"]:
    print(json.dumps({"error": "File already exists.", "path": payload["path"]}))
    raise SystemExit(1)

if payload["encoding"] == "base64":
    data = base64.b64decode(payload["content"])
else:
    data = payload["content"].encode("utf-8")

with open(target, "wb") as handle:
    handle.write(data)

print(json.dumps({
    "status": "success",
    "path": payload["path"],
    "bytes_written": len(data)
}))
""".strip()

        return await self._run_workspace_helper_async(
            session_id=session_id,
            persist_workspace=persist_workspace,
            helper_code=helper_code,
            payload={
                "path": normalized_path,
                "content": content,
                "encoding": encoding,
                "overwrite": overwrite
            }
        )

    async def read_file_async(
        self,
        session_id: str,
        path: str,
        encoding: str = "base64",
        persist_workspace: bool | None = None,
    ) -> dict:
        if persist_workspace is None:
            persist_workspace = self.default_persist_workspace

        normalized_path = self._normalize_workspace_path(path)
        if encoding not in {"utf-8", "base64"}:
            raise ValueError("encoding must be 'utf-8' or 'base64'.")

        helper_code = """
import base64
import json
from pathlib import Path

payload = json.loads(input())
target = Path("/workspace") / payload["path"]
if not target.exists() or not target.is_file():
    print(json.dumps({"error": "File not found.", "path": payload["path"]}))
    raise SystemExit(1)

data = target.read_bytes()
if payload["encoding"] == "base64":
    content = base64.b64encode(data).decode("ascii")
else:
    content = data.decode("utf-8")

print(json.dumps({
    "status": "success",
    "path": payload["path"],
    "encoding": payload["encoding"],
    "size": len(data),
    "content": content
}))
""".strip()

        return await self._run_workspace_helper_async(
            session_id=session_id,
            persist_workspace=persist_workspace,
            helper_code=helper_code,
            payload={
                "path": normalized_path,
                "encoding": encoding
            }
        )

    async def list_files_async(
        self,
        session_id: str,
        path: str = ".",
        persist_workspace: bool | None = None,
        recursive: bool = True,
    ) -> dict:
        if persist_workspace is None:
            persist_workspace = self.default_persist_workspace

        normalized_path = self._normalize_workspace_path(path)

        helper_code = """
import json
from pathlib import Path

payload = json.loads(input())
base = Path("/workspace")
target = base / payload["path"]
if not target.exists():
    print(json.dumps({"error": "Path not found.", "path": payload["path"]}))
    raise SystemExit(1)

if target.is_file():
    stat = target.stat()
    print(json.dumps({
        "status": "success",
        "path": payload["path"],
        "entries": [{
            "path": payload["path"],
            "type": "file",
            "size": stat.st_size
        }]
    }))
    raise SystemExit(0)

entries = []
iterator = target.rglob("*") if payload["recursive"] else target.iterdir()
for item in sorted(iterator):
    relative = item.relative_to(base).as_posix()
    item_type = "directory" if item.is_dir() else "file"
    size = item.stat().st_size if item.is_file() else None
    entries.append({
        "path": relative,
        "type": item_type,
        "size": size
    })

print(json.dumps({
    "status": "success",
    "path": payload["path"],
    "entries": entries
}))
""".strip()

        return await self._run_workspace_helper_async(
            session_id=session_id,
            persist_workspace=persist_workspace,
            helper_code=helper_code,
            payload={
                "path": normalized_path,
                "recursive": recursive
            }
        )

    async def purge_workspace_async(self, session_id: str) -> dict:
        volume_flag = self._build_workspace_mount(session_id, True)[1]
        volume_name = volume_flag.split(":", 1)[0]

        process = await asyncio.create_subprocess_exec(
            "docker", "volume", "rm", "-f", volume_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_bytes, stderr_bytes = await process.communicate()

        if process.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8").strip()
            raise RuntimeError(stderr_text or f"Failed to purge workspace volume {volume_name}.")

        return {
            "status": "success",
            "volume": volume_name,
            "details": stdout_bytes.decode("utf-8").strip()
        }
