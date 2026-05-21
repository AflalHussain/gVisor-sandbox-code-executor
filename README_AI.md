# Project Documentation: Secure Multi-Tenant Execution Engine (gVisor + FastAPI)

This document provides context for AI-assisted development. Use it to guide code generation, infrastructure debugging, and feature expansions.

## 1. System Architecture Blueprint

This platform safely runs untrusted multi-tenant AI agents and user-submitted Python/Shell code on standard, non-bare-metal Linux host environments.

```text
[External Client REST Request]
              |
              v
    [FastAPI Web Gateway] --(asyncio)--> [Docker CLI Wrapper Engine]
                                                     |
                                                     v
                                         [gVisor Runtime Context (runsc)]
                                                     |
                                        +------------+------------+
                                        v                         v
                                 [Gofer File Proxy]       [Sentry Kernel Proxy]
                                        |                         |
                                        v                         v
                                 [/workspace volume]       [Isolated Syscall Execution]
```

### Critical Security Boundaries

- **Kernel Isolation:** The default Docker engine (`runc`) is bypassed. The system forces the `--runtime=runsc` (gVisor) layer. System calls are intercepted by gVisor's user-space proxy kernel (Sentry).
- **File System Shielding:** Containers are blocked from accessing host directories. File operations are proxied through gVisor's file broker (Gofer).
- **Communication Pipe:** User payloads are passed directly into the container's standard input (`stdin`) to prevent terminal command-injection attacks on the host plane.
- **Hard Resource Constraints:** Memory is capped at `256m`, and processes are limited to `--pids-limit=20` to stop thread exhaustion (fork-bombs).
- **Configurable Workspace Persistence:** By default, `/workspace` can remain ephemeral per request, but the API may also bind a session-scoped Docker named volume when persistence is enabled. Persistent workspaces are keyed by `session_id` and can be purged explicitly.

## 2. Codebase Reference Artifacts

### Core Component A: Guest OS Build Blueprint (`Dockerfile`)

```dockerfile
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir numpy pandas requests pydantic

WORKDIR /sandbox
COPY executor.py /usr/local/bin/executor.py
RUN chmod +x /usr/local/bin/executor.py

# CRITICAL FOR GOFER: Pre-create /workspace with unrestricted access permissions
# before switching down to the unprivileged 'nobody' user account context.
RUN mkdir -p /workspace && chown -R nobody:nogroup /workspace && chmod 777 /workspace
VOLUME /workspace

USER nobody
```

### Core Component B: Internal Sandbox Script Runner (`executor.py`)

```python
import sys
import json
import subprocess

def main():
    try:
        input_data = sys.stdin.read()
        if not input_data.strip():
            print(json.dumps({"error": "Empty stdin payload context."}))
            sys.exit(1)

        payload = json.loads(input_data)
        code = payload.get("code", "")
        run_type = payload.get("type", "python")
        allowed_pip = payload.get("install_packages", [])

        # 1. Process dynamic user-approved dependencies over network bridge
        if allowed_pip:
            for package in allowed_pip:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--quiet", "--no-cache-dir", package],
                    check=False, capture_output=True
                )

        # 2. Fire execution ring with hard internal timeout ceiling
        if run_type == "shell":
            result = subprocess.run(["/bin/bash", "-c", code], capture_output=True, text=True, timeout=30)
        else:
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)

        print(json.dumps({
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }))

    except subprocess.TimeoutExpired:
        print(json.dumps({"error": "Execution timed out (Max 30s limit exceeded)."}))
    except Exception as e:
        print(json.dumps({"error": f"Internal Sandbox Crash Trace: {str(e)}"}))

if __name__ == "__main__":
    main()
```

### Core Component C: Host Platform Process Orchestration (`sandbox_engine.py`)

```python
import asyncio
import json

class SandboxEngine:
    def __init__(self, image_name="sandbox-executor:latest"):
        self.image_name = image_name

    async def run_code_async(self, code: str, run_type: str = "python", allowed_pip: list = None) -> dict:
        if allowed_pip is None:
            allowed_pip = []

        # Secure arguments array configuration Matrix
        cmd = [
            "docker", "run",
            "--interactive",
            "--runtime=runsc",
            "--network=bridge",
            "--dns=8.8.8.8",        # Bypasses local Ubuntu 127.0.0.53 loopback DNS constraints
            "--memory=256m",
            "--pids-limit=20",
            "--rm",                 # Guarantees dynamic cleanup on completion
            "-v", "/workspace",     # Triggers anonymous storage layer mapping matching Dockerfile permissions
            self.image_name,
            "python3", "/usr/local/bin/executor.py"
        ]

        payload = {"code": code, "type": run_type, "install_packages": allowed_pip}
        payload_bytes = json.dumps(payload).encode("utf-8")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout_bytes, stderr_bytes = await process.communicate(input=payload_bytes)

            if process.returncode != 0:
                err_msg = stderr_bytes.decode("utf-8").strip()
                return {"error": "Container runtime crash inside gVisor", "details": err_msg}

            return json.loads(stdout_bytes.decode("utf-8").strip())

        except Exception as e:
            return {"error": f"Orchestration layer runtime failure: {str(e)}"}
```

### Core Component D: REST Web API Gateway (`api_gateway.py`)

```python
import os
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List, Optional
from sandbox_engine import SandboxEngine

app = FastAPI(title="Secure Code Execution Engine", version="1.0")
engine = SandboxEngine()

API_KEY = os.getenv("SANDBOX_API_KEY", "super-secret-agent-token-123")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

async def verify_api_key(header_key: str = Depends(api_key_header)):
    if header_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key.")
    return header_key

class CodeExecutionRequest(BaseModel):
    code: str = Field(..., description="Raw script source text.")
    type: str = Field("python", description="'python' or 'shell'.")
    install_packages: Optional[List[str]] = Field(default=[])

@app.post("/api/v1/execute", dependencies=[Depends(verify_api_key)])
async def execute_code(request: CodeExecutionRequest):
    if request.type not in ["python", "shell"]:
        raise HTTPException(status_code=400, detail="Invalid target runtime engine profile.")

    return await engine.run_code_async(
        code=request.code,
        run_type=request.type,
        allowed_pip=request.install_packages
    )
```

## 3. Engineering Constraints & Rules for AI Prompts

When asking an AI model to add features or modify this code, enforce these strict engineering rules:

- **Never remove `--runtime=runsc`:** Do not let the AI switch the container back to standard `runc` for testing, as it destroys multi-tenant isolation.
- **No Direct Host Directory Mounting (`-v /host:/container`):** Any filesystem mapping must use anonymous container volumes or named volumes managed inside the container. Direct host binds cause gVisor Gofer proxy connection crashes due to permission conflicts with the `nobody` user.
- **Keep Stdin Pipe Routing Active:** Do not refactor the code to pass scripts via command line arguments (`docker run image python -c "code"`). This introduces bash-escaping exploits. Always pipe data using `process.communicate()`.
- **Network/DNS Restriction Rules:** The container must always include `--dns=8.8.8.8` (or another public provider) when using bridge networking. gVisor blocks routing lookups pointed at the host's local network system addresses (`127.0.0.53`).
- **Maintain Thread Async Integrity:** The API engine layer must avoid using blocking libraries (like the standard Docker Python SDK). All execution loops must remain non-blocking using Linux system processes via `asyncio.create_subprocess_exec`.

## 4. Workspace Persistence Controls

- `persist_workspace=true` on an execution request reuses a session-scoped Docker named volume mounted at `/workspace`.
- Omitting `persist_workspace` falls back to `SANDBOX_PERSISTENT_WORKSPACE_DEFAULT`.
- `SANDBOX_VOLUME_PREFIX` controls the prefix used for persistent Docker volume names.
- `DELETE /api/v1/session/{session_id}` purges the persistent workspace volume for that session.

## 5. Workspace File Staging APIs

- `POST /api/v1/session/{session_id}/files/write` writes a file into `/workspace` before execution.
- `POST /api/v1/session/{session_id}/files/read` reads a file back from `/workspace`.
- `POST /api/v1/session/{session_id}/files/list` lists files or directories in `/workspace`.
- File paths must be relative to `/workspace`; absolute paths and `..` traversal are rejected.
- `encoding="base64"` is the default transport for chatbot-style file uploads because it safely supports binary content.
- `encoding="utf-8"` can be used for plain text files when the caller already has text content.
