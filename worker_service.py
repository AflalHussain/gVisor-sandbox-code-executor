import logging
import os

from fastapi import Depends, FastAPI, Header, HTTPException

from sandbox_engine import SandboxEngine
from shared_models import (
    CodeExecutionRequest,
    WorkspaceListRequest,
    WorkspaceReadRequest,
    WorkspaceWriteRequest,
)


app = FastAPI(title="Secure Sandbox Worker", version="1.0")
engine = SandboxEngine()

LOG_LEVEL = os.getenv("SANDBOX_LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("sandbox.worker_service")

WORKER_API_KEY = os.getenv("SANDBOX_WORKER_API_KEY", "change-me-worker-secret")


async def verify_worker_auth(x_worker_auth: str = Header(default="")):
    if x_worker_auth != WORKER_API_KEY:
        logger.warning("worker_auth_failed")
        raise HTTPException(status_code=403, detail="Invalid worker authentication credential.")
    return x_worker_auth


@app.get("/internal/v1/health", dependencies=[Depends(verify_worker_auth)])
async def healthcheck():
    return {"status": "ok"}


@app.post("/internal/v1/execute", dependencies=[Depends(verify_worker_auth)])
async def execute_untrusted_payload(request: CodeExecutionRequest):
    if request.type not in ["python", "shell"]:
        raise HTTPException(status_code=400, detail="Unsupported execution type format. Use 'python' or 'shell'.")

    return await engine.run_code_async(
        code=request.code,
        run_type=request.type,
        allowed_pip=request.install_packages,
        session_id=request.session_id or "default",
        persist_workspace=request.persist_workspace,
    )


@app.post("/internal/v1/session/{session_id}/files/write", dependencies=[Depends(verify_worker_auth)])
async def write_session_file(session_id: str, request: WorkspaceWriteRequest):
    try:
        return await engine.write_file_async(
            session_id=session_id,
            path=request.path,
            content=request.content,
            encoding=request.encoding,
            persist_workspace=request.persist_workspace,
            overwrite=request.overwrite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/internal/v1/session/{session_id}/files/read", dependencies=[Depends(verify_worker_auth)])
async def read_session_file(session_id: str, request: WorkspaceReadRequest):
    try:
        return await engine.read_file_async(
            session_id=session_id,
            path=request.path,
            encoding=request.encoding,
            persist_workspace=request.persist_workspace,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/internal/v1/session/{session_id}/files/list", dependencies=[Depends(verify_worker_auth)])
async def list_session_files(session_id: str, request: WorkspaceListRequest):
    try:
        return await engine.list_files_async(
            session_id=session_id,
            path=request.path,
            recursive=request.recursive,
            persist_workspace=request.persist_workspace,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/internal/v1/session/{session_id}", dependencies=[Depends(verify_worker_auth)])
async def close_and_purge_session(session_id: str):
    try:
        return await engine.purge_workspace_async(session_id)
    except Exception as exc:
        logger.exception("purge_session_failed session_id=%s error=%s", session_id, str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
