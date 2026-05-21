"""
title: Sandbox Code Execution Toolkit
author: OpenAI Codex
description: >
  Execute Python or shell code and manage session workspace files through the
  Secure Code Execution Sandboxing API.

  ## Key rules for the AI model using these tools

  ### Python packages / dependencies
  ALWAYS pass required Python packages via the `install_packages` parameter.
  The sandbox installs them before running your code, so you must never embed
  `!pip install ...`, `subprocess.run(["pip", ...])`, or any other package-
  installation command inside the `code` string — those patterns do not work
  in this sandbox.

  Example — correct:
      execute_code(
          code="import pandas as pd; print(pd.__version__)",
          install_packages=["pandas"],
      )

  Example — WRONG (do not do this):
      execute_code(code="!pip install pandas\nimport pandas as pd")

  ### File paths when writing / reading files
  The sandbox's working directory is the root of the session workspace.
  ALWAYS use a bare filename (e.g. "report.pptx", "output.csv") when calling
  write_workspace_file or read_workspace_file unless you intentionally need a
  subdirectory.  Using absolute paths like "/tmp/report.pptx" or deep relative
  paths like "subdir/nested/report.pptx" is almost never what you want.

  Example — correct:
      write_workspace_file(path="report.pptx", content=b64_bytes, encoding="base64")

  Example — WRONG (do not do this):
      write_workspace_file(path="/tmp/report.pptx", ...)

version: 1.3.0
required_open_webui_version: 0.4.0
requirements: httpx,pydantic
license: MIT
"""

import mimetypes
import os
from typing import Any, Literal, Optional

import httpx
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _emit_status(
    event_emitter: Optional[object], description: str, done: bool
) -> None:
    """Push a status event to the Open WebUI chat UI.

    `done=True` marks the status as final (spinner stops).
    `done=False` keeps the spinner active while work is in progress.
    """
    if event_emitter:
        await event_emitter(  # type: ignore[misc]
            {
                "type": "status",
                "data": {
                    "description": description,
                    "done": done,
                },
            }
        )


def _resolve_session_id(
    session_id: Optional[str],
    user: Optional[dict],
    chat_id: Optional[str] = None,
) -> str:
    """Determine which sandbox session to target.

    Resolution order (first truthy value wins):
      1. The explicit `session_id` argument passed by the caller.
      2. The Open WebUI `__chat_id__` injected automatically per message.
      3. The `session_id` stored in the user's UserValves.

    Raises ValueError when none of the above are available, because every
    sandbox API call requires a session identifier.
    """
    if session_id:
        return session_id
    if chat_id:
        return chat_id
    if user and user.get("valves"):
        user_session_id = dict(user["valves"]).get("session_id", "")
        if user_session_id:
            return user_session_id
    raise ValueError(
        "session_id is required unless the tool is called from a chat context "
        "or set in UserValves."
    )


def _headers(api_key: str) -> dict[str, str]:
    """Build the HTTP request headers.

    The X-API-Key header is only added when an API key has been configured;
    omitting it is valid for sandboxes that run in an unsecured network.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _guess_media_type(path: str) -> str:
    """Return a best-guess MIME type for a file path.

    Falls back to 'application/octet-stream' for unknown extensions, which is
    the safe default for arbitrary binary downloads.
    """
    media_type, _ = mimetypes.guess_type(path)
    return media_type or "application/octet-stream"


async def _request(
    base_url: str,
    api_key: str,
    timeout_seconds: int,
    method: str,
    path: str,
    payload: Optional[dict] = None,
) -> dict:
    """Send a single async HTTP request to the sandbox API and return the JSON body.

    Raises httpx.HTTPStatusError on non-2xx responses so callers can catch it
    and surface a clean error message instead of a raw stack trace.
    """
    url = f"{base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.request(
            method,
            url,
            headers=_headers(api_key),
            json=payload,
        )
        response.raise_for_status()
        return response.json()


# ---------------------------------------------------------------------------
# Tools class — each public async method is exposed as an Open WebUI tool
# ---------------------------------------------------------------------------

class Tools:
    """Open WebUI tool bundle for sandboxed code execution and file management.

    All methods share the same session workspace identified by `session_id`.
    When `persist_workspace=True` (the default) files written in one call are
    accessible in later calls within the same session.
    """

    class Valves(BaseModel):
        """Admin-level configuration set once per deployment.

        Values fall back to environment variables so the admin can configure
        the toolkit without touching source code.
        """

        base_url: str = Field(
            default=os.getenv("SANDBOX_API_BASE_URL", "http://localhost:8088"),
            description=(
                "Base URL for the sandbox API service, e.g. http://sandbox:8088. "
                "Set SANDBOX_API_BASE_URL in the environment to override without "
                "editing this file."
            ),
        )
        api_key: str = Field(
            default=os.getenv("SANDBOX_API_KEY", ""),
            description=(
                "API key sent as X-API-Key header. Leave empty when the sandbox "
                "runs in a trusted network with no authentication required. "
                "Set SANDBOX_API_KEY in the environment to supply it securely."
            ),
        )
        default_persist_workspace: bool = Field(
            default=os.getenv("SANDBOX_PERSIST_WORKSPACE_DEFAULT", "true").lower()
            == "true",
            description=(
                "Whether to persist the session workspace by default. "
                "When True, files survive between separate tool calls in the same "
                "chat session. Individual tool calls can override this per-call."
            ),
        )
        timeout_seconds: int = Field(
            default=int(os.getenv("SANDBOX_API_TIMEOUT_SECONDS", "120")),
            description=(
                "HTTP timeout (seconds) for sandbox API requests. Increase this "
                "for workloads that install many packages or run slow computations."
            ),
        )

    class UserValves(BaseModel):
        """Per-user overrides that individual users can set in their profile."""

        session_id: str = Field(
            default="",
            description=(
                "Optional fixed session ID. When set, every tool call from this "
                "user targets the same workspace regardless of which chat is active. "
                "Leave empty to use the current chat ID automatically."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # execute_code
    # ------------------------------------------------------------------

    async def execute_code(
        self,
        code: str,
        run_type: Literal["python", "shell"] = "python",
        session_id: Optional[str] = None,
        persist_workspace: Optional[bool] = None,
        install_packages: Optional[list[str]] = None,
        __user__: Optional[dict[str, Any]] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Optional[object] = None,
    ) -> dict:
        """Execute Python or shell code inside the sandbox.

        ## Parameters

        **code** (required)
          The source code to run.  For `run_type="python"` this is a Python
          script.  For `run_type="shell"` it is a POSIX shell script (bash).

        **run_type** (default: "python")
          Choose "python" for Python scripts or "shell" for shell commands.

        **install_packages** — *** ALWAYS USE THIS FOR DEPENDENCIES ***
          List of PyPI package names to install *before* the code runs.
          This is the ONLY supported way to install Python packages.

          Correct usage:
              install_packages=["pandas", "matplotlib", "python-pptx==0.6.23"]

          Why not `!pip install` in the code string?
          - The sandbox does not support IPython magic commands (`!cmd`).
          - Running pip as a subprocess inside the code block is unreliable and
            can interfere with the sandbox environment.
          - Package installation via `install_packages` is performed by the
            sandbox executor in a controlled, reproducible way before code runs.

        **session_id** (optional)
          Sandbox session to use.  Defaults to the current Open WebUI chat ID,
          so files written in one call are automatically visible in the next
          call within the same chat.

        **persist_workspace** (optional)
          Override the admin default.  Set to True to keep files after
          execution, False for a clean ephemeral run.

        ## File path convention
        Code running in the sandbox starts in the session workspace root.
        Save output files with bare filenames:

            import pandas as pd
            df.to_csv("results.csv")   # ✓ saves to workspace root

        Avoid absolute or deeply nested paths unless explicitly needed.

        ## Print statements — ALWAYS include success and failure output
        Every script passed as `code` MUST end with a print statement that
        confirms the outcome.  This is the only way the user can see whether
        the operation succeeded or failed, because stdout is the primary
        feedback channel from the sandbox.

        Rules:
          - On success, print a clear confirmation that names the output
            artefact, e.g.:
                print("presentation.pptx was created successfully")
                print("report.csv was created successfully")
                print("chart.png was saved successfully")
          - On failure, catch the exception and print a descriptive error, e.g.:
                except Exception as e:
                    print(f"Failed to create presentation.pptx: {e}")
          - Never leave the happy path silent.  A script that produces a file
            but prints nothing gives the user no confirmation.

        Template every script should follow:

            try:
                # ... your logic here ...
                print("<output_file> was created successfully")
            except Exception as e:
                print(f"Failed to create <output_file>: {e}")

        ## Return value
        A dict with at minimum:
          - "stdout": captured standard output
          - "stderr": captured standard error
          - "exit_code": integer exit code (0 = success)
        """
        if install_packages is None:
            install_packages = []
        if persist_workspace is None:
            persist_workspace = self.valves.default_persist_workspace

        # The sandbox executor installs packages first, then runs the code.
        payload = {
            "code": code,
            "type": run_type,
            "install_packages": install_packages,
            "persist_workspace": persist_workspace,
        }

        # Attach the session ID so the executor maps this run to the right workspace.
        resolved_session_id = _resolve_session_id(
            session_id, __user__, __chat_id__
        )
        if resolved_session_id:
            payload["session_id"] = resolved_session_id

        await _emit_status(
            __event_emitter__, "Executing code in sandbox...", False
        )
        try:
            result = await _request(
                self.valves.base_url,
                self.valves.api_key,
                self.valves.timeout_seconds,
                "POST",
                "/api/v1/execute",
                payload,
            )
            await _emit_status(
                __event_emitter__, "Sandbox execution completed.", True
            )
            return result
        except Exception as exc:
            await _emit_status(
                __event_emitter__,
                f"Sandbox execution failed: {exc}",
                True,
            )
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # write_workspace_file
    # ------------------------------------------------------------------

    async def write_workspace_file(
        self,
        path: str,
        content: str,
        session_id: Optional[str] = None,
        encoding: Literal["utf-8", "base64"] = "utf-8",
        overwrite: bool = True,
        persist_workspace: Optional[bool] = None,
        __user__: Optional[dict[str, Any]] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Optional[object] = None,
    ) -> dict:
        """Write a file into the session workspace.

        ## Parameters

        **path** — *** USE A BARE FILENAME ***
          The destination path inside the session workspace.
          The sandbox working directory IS the session workspace root, so a
          bare filename places the file exactly where subsequent code can read it:

              path="report.pptx"    # ✓ correct — file lands in workspace root
              path="output.csv"     # ✓ correct
              path="/tmp/out.csv"   # ✗ wrong — absolute path outside workspace
              path="sub/out.csv"    # ✗ avoid unless the subdir is intentional

          Using bare filenames also ensures `execute_code` can open the file
          without any path manipulation:

              open("report.pptx", "rb")   # works when path="report.pptx"

        **content** (required)
          The file content as a string.  For text files pass the raw text.
          For binary files encode to base64 first and set `encoding="base64"`.

        **encoding** (default: "utf-8")
          "utf-8"  — plain text content, written as-is.
          "base64" — binary content encoded as a base64 string; the sandbox
                     decodes it before writing to disk.  Use this for .pptx,
                     .pdf, .zip, images, and any non-text format.

        **overwrite** (default: True)
          Whether to replace an existing file at the same path.

        **persist_workspace** (optional)
          Override the admin default for this single call.

        ## Return value
        A dict with:
          - "status": "success" or "error"
          - "path": the path that was written
          - "size": number of bytes written
        """
        if persist_workspace is None:
            persist_workspace = self.valves.default_persist_workspace
        resolved_session_id = _resolve_session_id(
            session_id, __user__, __chat_id__
        )

        await _emit_status(
            __event_emitter__, f"Writing workspace file: {path}", False
        )
        try:
            result = await _request(
                self.valves.base_url,
                self.valves.api_key,
                self.valves.timeout_seconds,
                "POST",
                f"/api/v1/session/{resolved_session_id}/files/write",
                {
                    "path": path,
                    "content": content,
                    "encoding": encoding,
                    "overwrite": overwrite,
                    "persist_workspace": persist_workspace,
                },
            )
            await _emit_status(
                __event_emitter__, f"Workspace file written: {path}", True
            )
            return result
        except Exception as exc:
            await _emit_status(
                __event_emitter__, f"File write failed: {exc}", True
            )
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # read_workspace_file
    # ------------------------------------------------------------------

    async def read_workspace_file(
        self,
        path: str,
        session_id: Optional[str] = None,
        encoding: Literal["utf-8", "base64"] = "utf-8",
        persist_workspace: Optional[bool] = None,
        __user__: Optional[dict[str, Any]] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Optional[object] = None,
    ) -> dict:
        """Read a file from the session workspace.

        ## Parameters

        **path** — *** USE A BARE FILENAME ***
          Path of the file inside the session workspace.  Use the same bare
          filename you passed to write_workspace_file or that execute_code
          wrote to disk:

              path="results.csv"   # ✓ reads workspace root file
              path="output.png"    # ✓ reads workspace root file

        **encoding** (default: "utf-8")
          "utf-8"  — returns the file as a plain text string in `content`.
          "base64" — returns the raw bytes base64-encoded in `content`.
                     Use this for binary files (.pptx, .pdf, images, etc.)
                     so the content is not corrupted by text decoding.

        **persist_workspace** (optional)
          Override the admin default for this single call.

        ## Return value
        A dict with:
          - "status": "success" or "error"
          - "path": path that was read
          - "encoding": the encoding used ("utf-8" or "base64")
          - "content": the file content (string or base64 string)
          - "size": number of bytes
        """
        if persist_workspace is None:
            persist_workspace = self.valves.default_persist_workspace
        resolved_session_id = _resolve_session_id(
            session_id, __user__, __chat_id__
        )

        await _emit_status(
            __event_emitter__, f"Reading workspace file: {path}", False
        )
        try:
            result = await _request(
                self.valves.base_url,
                self.valves.api_key,
                self.valves.timeout_seconds,
                "POST",
                f"/api/v1/session/{resolved_session_id}/files/read",
                {
                    "path": path,
                    "encoding": encoding,
                    "persist_workspace": persist_workspace,
                },
            )
            await _emit_status(
                __event_emitter__, f"Workspace file read: {path}", True
            )
            return result
        except Exception as exc:
            await _emit_status(
                __event_emitter__, f"File read failed: {exc}", True
            )
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # get_workspace_download
    # ------------------------------------------------------------------

    async def get_workspace_download(
        self,
        path: str,
        session_id: Optional[str] = None,
        persist_workspace: Optional[bool] = None,
        __user__: Optional[dict[str, Any]] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Optional[object] = None,
    ) -> dict:
        """Return a workspace file in a download-friendly envelope for binary outputs.

        This is a convenience wrapper around read_workspace_file that always
        uses base64 encoding and adds UI-friendly metadata fields.

        ## When to use this instead of read_workspace_file
        Use get_workspace_download for files the user wants to download, such as:
          - Office documents (.pptx, .docx, .xlsx)
          - PDFs (.pdf)
          - Archives (.zip, .tar.gz)
          - Images (.png, .jpg, .svg)

        Use read_workspace_file directly when you only need the raw text content
        of a plain-text file (e.g. .csv, .txt, .json, .log).

        ## Parameters

        **path** — bare filename in the workspace root, e.g. "report.pptx".

        **persist_workspace** (optional) — override the admin default.

        ## Return value
        A dict with:
          - "status": "success"
          - "session_id": the resolved session ID
          - "path": path that was read
          - "filename": basename extracted from `path` (used as download filename)
          - "media_type": guessed MIME type (e.g. "application/vnd.ms-powerpoint")
          - "encoding": always "base64"
          - "content_base64": the file's bytes as a base64 string
          - "size": number of bytes (may be None if the API omits it)
        """
        if persist_workspace is None:
            persist_workspace = self.valves.default_persist_workspace
        resolved_session_id = _resolve_session_id(
            session_id, __user__, __chat_id__
        )

        await _emit_status(
            __event_emitter__, f"Preparing workspace download: {path}", False
        )
        try:
            result = await _request(
                self.valves.base_url,
                self.valves.api_key,
                self.valves.timeout_seconds,
                "POST",
                f"/api/v1/session/{resolved_session_id}/files/read",
                {
                    "path": path,
                    "encoding": "base64",
                    "persist_workspace": persist_workspace,
                },
            )
            if "error" in result:
                await _emit_status(
                    __event_emitter__, f"Workspace download failed: {path}", True
                )
                return result

            # Build the download envelope the UI needs to render a download link.
            download_result = {
                "status": result.get("status", "success"),
                "session_id": resolved_session_id,
                "persist_workspace": persist_workspace,
                "path": path,
                "filename": os.path.basename(path) or path,
                "media_type": _guess_media_type(path),
                "size": result.get("size"),
                "encoding": "base64",
                "content_base64": result.get("content", ""),
            }
            await _emit_status(
                __event_emitter__, f"Workspace download ready: {path}", True
            )
            return download_result
        except Exception as exc:
            await _emit_status(
                __event_emitter__, f"Workspace download failed: {exc}", True
            )
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # list_workspace_files
    # ------------------------------------------------------------------

    async def list_workspace_files(
        self,
        path: str = ".",
        session_id: Optional[str] = None,
        recursive: bool = True,
        persist_workspace: Optional[bool] = None,
        __user__: Optional[dict[str, Any]] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Optional[object] = None,
    ) -> dict:
        """List files and directories in the session workspace.

        ## Parameters

        **path** (default: ".")
          Directory to list inside the workspace.  Use "." to list the workspace
          root (all top-level files the current session has created or written).

        **recursive** (default: True)
          True  — return the full file tree under `path`.
          False — return only the direct children (non-recursive ls).

        **persist_workspace** (optional) — override the admin default.

        ## Return value
        A dict with:
          - "status": "success" or "error"
          - "path": the directory that was listed
          - "files": list of file/directory entries, each with at minimum
              - "name": filename or directory name
              - "path": relative path from workspace root
              - "type": "file" or "directory"
              - "size": file size in bytes (0 for directories)
        """
        if persist_workspace is None:
            persist_workspace = self.valves.default_persist_workspace
        resolved_session_id = _resolve_session_id(
            session_id, __user__, __chat_id__
        )

        await _emit_status(
            __event_emitter__, f"Listing workspace path: {path}", False
        )
        try:
            result = await _request(
                self.valves.base_url,
                self.valves.api_key,
                self.valves.timeout_seconds,
                "POST",
                f"/api/v1/session/{resolved_session_id}/files/list",
                {
                    "path": path,
                    "recursive": recursive,
                    "persist_workspace": persist_workspace,
                },
            )
            await _emit_status(
                __event_emitter__, f"Workspace path listed: {path}", True
            )
            return result
        except Exception as exc:
            await _emit_status(__event_emitter__, f"List failed: {exc}", True)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # purge_session_workspace
    # ------------------------------------------------------------------

    async def purge_session_workspace(
        self,
        session_id: Optional[str] = None,
        __user__: Optional[dict[str, Any]] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Optional[object] = None,
    ) -> dict:
        """Delete all files in the persistent workspace for a session.

        Use this to free storage or start fresh.  The session itself is not
        destroyed — new code executions after purging will begin with an empty
        workspace.

        ## Parameters

        **session_id** (optional)
          Which session to purge.  Defaults to the current chat ID.
          Only purge sessions you own; there is no undo.

        ## Return value
        A dict with:
          - "status": "success" or "error"
          - "session_id": the session that was purged
        """
        resolved_session_id = _resolve_session_id(
            session_id, __user__, __chat_id__
        )

        await _emit_status(
            __event_emitter__,
            f"Purging session workspace: {resolved_session_id}",
            False,
        )
        try:
            result = await _request(
                self.valves.base_url,
                self.valves.api_key,
                self.valves.timeout_seconds,
                "DELETE",
                f"/api/v1/session/{resolved_session_id}",
            )
            await _emit_status(
                __event_emitter__,
                f"Session workspace purged: {resolved_session_id}",
                True,
            )
            return result
        except Exception as exc:
            await _emit_status(
                __event_emitter__, f"Workspace purge failed: {exc}", True
            )
            return {"error": str(exc)}
