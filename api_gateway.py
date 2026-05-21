# api_gateway.py
import json
import logging
import os
import uuid
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List, Optional
from sandbox_engine import SandboxEngine

app = FastAPI(title="Secure Code Execution Sandboxing API", version="1.0")
engine = SandboxEngine()

LOG_LEVEL = os.getenv("SANDBOX_LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("sandbox.api_gateway")

# Production Security: Set an API token in your environment variables
API_KEY = os.getenv("SANDBOX_API_KEY", "super-secret-agent-token-123")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)
 
async def verify_api_key(header_key: str = Depends(api_key_header)):
    if header_key != API_KEY:
        logger.warning("api_key_validation_failed")
        raise HTTPException(status_code=403, detail="Invalid API Key authentication credential.")
    logger.debug("api_key_validation_succeeded")
    return header_key


def _resolved_persist_workspace(request_value: Optional[bool]) -> bool:
    return request_value if request_value is not None else engine.default_persist_workspace


def _format_code_debug(request_id: str, session_id: str, run_type: str, code: str) -> str:
    lines = code.splitlines()
    numbered = "\n".join(f"  {i+1:>4} | {line}" for i, line in enumerate(lines))
    return (
        f"\n[execute request_id={request_id} session_id={session_id} type={run_type}]\n"
        f"--- code ({len(lines)} lines) ---\n"
        f"{numbered}\n"
        f"--- end code ---"
    )


def _format_result_debug(request_id: str, session_id: str, result: dict) -> str:
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    exit_code = result.get("exit_code", "n/a")
    status = result.get("status", "n/a")
    error = result.get("error")

    parts = [
        f"\n[execute request_id={request_id} session_id={session_id} status={status} exit_code={exit_code}]"
    ]
    if error:
        parts.append(f"--- error ---\n{error}")
    parts.append(f"--- stdout ---\n{stdout if stdout else '(empty)'}")
    parts.append(f"--- stderr ---\n{stderr if stderr else '(empty)'}")
    parts.append("--- end result ---")
    return "\n".join(parts)


def _truncate_debug_text(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n... (truncated, total_length={len(value)})"


def _format_workspace_request_debug(
    endpoint: str, request_id: str, session_id: str, payload: dict
) -> str:
    return (
        f"\n[{endpoint} request_id={request_id} session_id={session_id}]\n"
        f"--- request payload ---\n"
        f"{_truncate_debug_text(json.dumps(payload, indent=2, ensure_ascii=False))}\n"
        f"--- end request ---"
    )


def _format_workspace_result_debug(
    endpoint: str, request_id: str, session_id: str, result: dict
) -> str:
    return (
        f"\n[{endpoint} request_id={request_id} session_id={session_id}]\n"
        f"--- result payload ---\n"
        f"{_truncate_debug_text(json.dumps(result, indent=2, ensure_ascii=False))}\n"
        f"--- end result ---"
    )


def _result_summary(result: dict) -> dict:
    summary = {}
    for key in ("status", "error", "exit_code", "path", "volume"):
        if key in result:
            summary[key] = result[key]
    if "stdout" in result:
        summary["stdout_len"] = len(result.get("stdout", ""))
    if "stderr" in result:
        summary["stderr_len"] = len(result.get("stderr", ""))
    if "entries" in result and isinstance(result["entries"], list):
        summary["entries_count"] = len(result["entries"])
    if "bytes_written" in result:
        summary["bytes_written"] = result["bytes_written"]
    if "size" in result:
        summary["size"] = result["size"]
    return summary

# Define Data Input Validation Structures
class CodeExecutionRequest(BaseModel):
    code: str = Field(..., description="The raw code snippet payload text to execute.")
    type: str = Field("python", description="Language runner profile target: 'python' or 'shell'.")
    install_packages: Optional[List[str]] = Field(default=[], description="User-approved pip modules list.")
    session_id: Optional[str] = Field(default=None, description="Optional custom session ID used to address a workspace.")
    persist_workspace: Optional[bool] = Field(
        default=None,
        description="When true, reuses a Docker named volume for the session. When omitted, the server default is used."
    )

class WorkspaceWriteRequest(BaseModel):
    path: str = Field(..., description="Relative path inside /workspace.")
    content: str = Field(..., description="File content as utf-8 text or base64-encoded bytes.")
    encoding: str = Field("base64", description="'utf-8' for text or 'base64' for arbitrary binary files.")
    overwrite: bool = Field(True, description="When false, the request fails if the file already exists.")
    persist_workspace: Optional[bool] = Field(
        default=None,
        description="When true, reuses a Docker named volume for the session. When omitted, the server default is used."
    )

class WorkspaceReadRequest(BaseModel):
    path: str = Field(..., description="Relative path inside /workspace.")
    encoding: str = Field("base64", description="'utf-8' for text or 'base64' for arbitrary binary files.")
    persist_workspace: Optional[bool] = Field(
        default=None,
        description="When true, reads from the persistent workspace volume for the session. When omitted, the server default is used."
    )

class WorkspaceListRequest(BaseModel):
    path: str = Field(".", description="Relative file or directory path inside /workspace.")
    recursive: bool = Field(True, description="When true, lists descendants recursively for directories.")
    persist_workspace: Optional[bool] = Field(
        default=None,
        description="When true, lists from the persistent workspace volume for the session. When omitted, the server default is used."
    )

@app.post("/api/v1/execute", dependencies=[Depends(verify_api_key)])
async def execute_untrusted_payload(request: CodeExecutionRequest):
    """Securely posts an untrusted Python or Shell script payload to the gVisor engine."""
    request_id = uuid.uuid4().hex[:12]
    if request.type not in ["python", "shell"]:
        logger.warning(
            "execute_request_rejected request_id=%s run_type=%s",
            request_id,
            request.type
        )
        raise HTTPException(status_code=400, detail="Unsupported execution type format. Use 'python' or 'shell'.")
    
    # Generate a unique workspace session identifier if none was provided
    session_id = request.session_id or f"session_{uuid.uuid4().hex[:12]}"
    persist_workspace = _resolved_persist_workspace(request.persist_workspace)
    logger.info(
        "execute_request_started request_id=%s session_id=%s run_type=%s persist_workspace=%s install_packages_count=%s install_packages=%s code_len=%s",
        request_id,
        session_id,
        request.type,
        persist_workspace,
        len(request.install_packages or []),
        request.install_packages or [],
        len(request.code)
    )
    logger.debug(_format_code_debug(request_id, session_id, request.type, request.code))

    # Fire off execution directly to our non-blocking sub-process engine
    result = await engine.run_code_async(
        code=request.code,
        run_type=request.type,
        allowed_pip=request.install_packages,
        session_id=session_id,
        persist_workspace=request.persist_workspace
    )
    
    # Append the active storage token tracking descriptor back to client context
    result["session_id"] = session_id
    result["persist_workspace"] = persist_workspace
    logger.info(
        "execute_request_completed request_id=%s session_id=%s result=%s",
        request_id,
        session_id,
        _result_summary(result)
    )
    logger.debug(_format_result_debug(request_id, session_id, result))
    return result

@app.post("/api/v1/session/{session_id}/files/write", dependencies=[Depends(verify_api_key)])
async def write_session_file(session_id: str, request: WorkspaceWriteRequest):
    request_id = uuid.uuid4().hex[:12]
    persist_workspace = _resolved_persist_workspace(request.persist_workspace)
    request_payload = {
        "path": request.path,
        "content": request.content,
        "encoding": request.encoding,
        "overwrite": request.overwrite,
        "persist_workspace": persist_workspace,
    }
    logger.info(
        "write_file_started request_id=%s session_id=%s path=%s encoding=%s overwrite=%s persist_workspace=%s content_len=%s",
        request_id,
        session_id,
        request.path,
        request.encoding,
        request.overwrite,
        persist_workspace,
        len(request.content)
    )
    logger.debug(
        _format_workspace_request_debug(
            "write_file", request_id, session_id, request_payload
        )
    )
    try:
        result = await engine.write_file_async(
            session_id=session_id,
            path=request.path,
            content=request.content,
            encoding=request.encoding,
            persist_workspace=request.persist_workspace,
            overwrite=request.overwrite
        )
        result["session_id"] = session_id
        result["persist_workspace"] = persist_workspace
        logger.info(
            "write_file_completed request_id=%s session_id=%s result=%s",
            request_id,
            session_id,
            _result_summary(result)
        )
        logger.debug(
            _format_workspace_result_debug(
                "write_file", request_id, session_id, result
            )
        )
        return result
    except ValueError as e:
        logger.warning(
            "write_file_validation_failed request_id=%s session_id=%s path=%s error=%s",
            request_id,
            session_id,
            request.path,
            str(e)
        )
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/v1/session/{session_id}/files/read", dependencies=[Depends(verify_api_key)])
async def read_session_file(session_id: str, request: WorkspaceReadRequest):
    request_id = uuid.uuid4().hex[:12]
    persist_workspace = _resolved_persist_workspace(request.persist_workspace)
    request_payload = {
        "path": request.path,
        "encoding": request.encoding,
        "persist_workspace": persist_workspace,
    }
    logger.info(
        "read_file_started request_id=%s session_id=%s path=%s encoding=%s persist_workspace=%s",
        request_id,
        session_id,
        request.path,
        request.encoding,
        persist_workspace
    )
    logger.debug(
        _format_workspace_request_debug(
            "read_file", request_id, session_id, request_payload
        )
    )
    try:
        result = await engine.read_file_async(
            session_id=session_id,
            path=request.path,
            encoding=request.encoding,
            persist_workspace=request.persist_workspace
        )
        result["session_id"] = session_id
        result["persist_workspace"] = persist_workspace
        logger.info(
            "read_file_completed request_id=%s session_id=%s result=%s",
            request_id,
            session_id,
            _result_summary(result)
        )
        logger.debug(
            _format_workspace_result_debug(
                "read_file", request_id, session_id, result
            )
        )
        return result
    except ValueError as e:
        logger.warning(
            "read_file_validation_failed request_id=%s session_id=%s path=%s error=%s",
            request_id,
            session_id,
            request.path,
            str(e)
        )
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/v1/session/{session_id}/files/list", dependencies=[Depends(verify_api_key)])
async def list_session_files(session_id: str, request: WorkspaceListRequest):
    request_id = uuid.uuid4().hex[:12]
    persist_workspace = _resolved_persist_workspace(request.persist_workspace)
    request_payload = {
        "path": request.path,
        "recursive": request.recursive,
        "persist_workspace": persist_workspace,
    }
    logger.info(
        "list_files_started request_id=%s session_id=%s path=%s recursive=%s persist_workspace=%s",
        request_id,
        session_id,
        request.path,
        request.recursive,
        persist_workspace
    )
    logger.debug(
        _format_workspace_request_debug(
            "list_files", request_id, session_id, request_payload
        )
    )
    try:
        result = await engine.list_files_async(
            session_id=session_id,
            path=request.path,
            recursive=request.recursive,
            persist_workspace=request.persist_workspace
        )
        result["session_id"] = session_id
        result["persist_workspace"] = persist_workspace
        logger.info(
            "list_files_completed request_id=%s session_id=%s result=%s",
            request_id,
            session_id,
            _result_summary(result)
        )
        logger.debug(
            _format_workspace_result_debug(
                "list_files", request_id, session_id, result
            )
        )
        return result
    except ValueError as e:
        logger.warning(
            "list_files_validation_failed request_id=%s session_id=%s path=%s error=%s",
            request_id,
            session_id,
            request.path,
            str(e)
        )
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/v1/session/{session_id}", dependencies=[Depends(verify_api_key)])
async def close_and_purge_session(session_id: str):
    """Wipes the persistent workspace volume for a specific session ID."""
    request_id = uuid.uuid4().hex[:12]
    logger.info("purge_session_started request_id=%s session_id=%s", request_id, session_id)
    logger.debug(
        _format_workspace_request_debug(
            "purge_session", request_id, session_id, {"session_id": session_id}
        )
    )
    try:
        purge_result = await engine.purge_workspace_async(session_id)
        result = {
            "status": "success",
            "message": f"Workspace storage data bucket for {session_id} successfully purged.",
            **purge_result
        }
        logger.info(
            "purge_session_completed request_id=%s session_id=%s result=%s",
            request_id,
            session_id,
            _result_summary(result)
        )
        logger.debug(
            _format_workspace_result_debug(
                "purge_session", request_id, session_id, result
            )
        )
        return result
    except Exception as e:
        logger.exception(
            "purge_session_failed request_id=%s session_id=%s error=%s",
            request_id,
            session_id,
            str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))
