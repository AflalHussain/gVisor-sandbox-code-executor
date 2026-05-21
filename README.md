# Secure Sandbox Execution API

A small FastAPI service for running untrusted Python or shell code inside a gVisor-backed Docker container, with optional session-based persistent workspaces and file staging endpoints.

This project is designed to be used directly over HTTP or through the included Open WebUI tool file, [`openwebui_sandbox_tools.py`](/hms/apps/sandbox-test/openwebui_sandbox_tools.py:1).

## What It Does

- Executes untrusted `python` or `shell` snippets inside a container started with `--runtime=runsc`
- Supports optional per-session persistent workspaces mounted at `/workspace`
- Lets clients write, read, list, and purge workspace files over HTTP
- Uses API key authentication via the `X-API-Key` header
- Includes an OpenAPI spec and an Open WebUI tool wrapper

## Project Layout

- [`api_gateway.py`](/hms/apps/sandbox-test/api_gateway.py:1): FastAPI app and HTTP endpoints
- [`sandbox_engine.py`](/hms/apps/sandbox-test/sandbox_engine.py:1): Docker/gVisor orchestration layer
- [`executor.py`](/hms/apps/sandbox-test/executor.py:1): Code that runs inside the sandbox container
- [`openwebui_sandbox_tools.py`](/hms/apps/sandbox-test/openwebui_sandbox_tools.py:1): Open WebUI tool integration
- [`openapi.yaml`](/hms/apps/sandbox-test/openapi.yaml:1): API contract
- [`tests/`](/hms/apps/sandbox-test/tests): unit and integration tests

## How It Works

1. A client sends code to `POST /api/v1/execute`.
2. The API validates the request and forwards it to `SandboxEngine`.
3. `SandboxEngine` starts a Docker container with gVisor (`runsc`).
4. The request payload is piped through `stdin` into [`executor.py`](/hms/apps/sandbox-test/executor.py:1).
5. The executor optionally installs approved pip packages, runs the code, and returns JSON.

For workspace operations, the engine launches short-lived helper containers that mount the same `/workspace` volume and perform file reads/writes/listing safely inside the sandbox boundary.

## Security Model

Current protections in code:

- gVisor runtime via `--runtime=runsc`
- No direct code passed through host shell arguments; payloads are sent over `stdin`
- Workspace path normalization rejects absolute paths and `..` traversal
- File operations are constrained to `/workspace`
- API key authentication on all public endpoints
- Memory limit on containers with `--memory=256m`
- `--network=none` for workspace helper containers

Important notes:

- Code execution containers use `--network=bridge` and `--dns=8.8.8.8`, so executed code can reach the network unless you change that behavior in [`sandbox_engine.py`](/hms/apps/sandbox-test/sandbox_engine.py:74).
- Dynamic `pip install` is allowed for packages explicitly passed in `install_packages`.
- The `--pids-limit` setting is currently commented out in [`sandbox_engine.py`](/hms/apps/sandbox-test/sandbox_engine.py:87).

## Requirements

- Python 3.11+
- Docker
- gVisor installed and configured as the Docker runtime `runsc`

Host prerequisites:

- Linux host
- `x86_64` or `arm64` architecture
- Linux kernel `4.14.77+`
- Docker `17.09.0+`

Python dependencies are listed in [`requirements.txt`](/hms/apps/sandbox-test/requirements.txt:1):

- `fastapi`
- `httpx`
- `requests`
- `uvicorn`

## Host Prerequisites

Before running this service, make sure the host can launch Docker containers with the `runsc` runtime.

### 1. Install Docker

Install Docker first if it is not already available.

Quick check:

```bash
docker --version
```

### 2. Install gVisor

You have two common options on Linux.

Option A: install from the gVisor apt repository

```bash
sudo apt-get update && \
sudo apt-get install -y \
  apt-transport-https \
  ca-certificates \
  curl \
  gnupg

curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" | sudo tee /etc/apt/sources.list.d/gvisor.list > /dev/null
sudo apt-get update && sudo apt-get install -y runsc
```

Option B: manual install of the latest release

```bash
(
  set -e
  ARCH=$(uname -m)
  URL=https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}
  wget ${URL}/runsc ${URL}/runsc.sha512 \
    ${URL}/containerd-shim-runsc-v1 ${URL}/containerd-shim-runsc-v1.sha512
  sha512sum -c runsc.sha512 \
    -c containerd-shim-runsc-v1.sha512
  rm -f *.sha512
  chmod a+rx runsc containerd-shim-runsc-v1
  sudo mv runsc containerd-shim-runsc-v1 /usr/local/bin
)
```

### 3. Register `runsc` with Docker

If `runsc` was not configured automatically, register it with Docker:

```bash
sudo runsc install
sudo systemctl restart docker
```

You can also configure Docker manually in `/etc/docker/daemon.json`. For example:

```json
{
  "runtimes": {
    "runsc": {
      "path": "/usr/local/bin/runsc",
      "runtimeArgs": [
        "--debug",
        "--debug-log=/tmp/gvisor-debug/"
      ]
    }
  }
}
```

After editing `/etc/docker/daemon.json`, restart Docker:

```bash
sudo systemctl restart docker
```

Notes:

- `runtimeArgs` are optional and useful for debugging gVisor runtime issues.
- The debug log directory must be writable by the runtime.
- If `daemon.json` already contains other Docker settings, merge the `runtimes` block into the existing JSON instead of replacing the whole file.

### 4. Verify the runtime

Confirm Docker can use gVisor:

```bash
docker run --runtime=runsc --rm hello-world
docker run --runtime=runsc --rm -it ubuntu dmesg
```

If the second command prints lines beginning with `Starting gVisor...`, the runtime is active.

## Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Build the sandbox image

From the project directory:

```bash
docker build -t sandbox-executor:latest .
```

### 3. Set environment variables

```bash
export SANDBOX_API_KEY="change-me"
export SANDBOX_LOG_LEVEL="INFO"
export SANDBOX_PERSISTENT_WORKSPACE_DEFAULT="false"
export SANDBOX_VOLUME_PREFIX="sandbox-session"
```

Available environment variables:

- `SANDBOX_API_KEY`: API key required by the service
- `SANDBOX_LOG_LEVEL`: logging level for the API
- `SANDBOX_PERSISTENT_WORKSPACE_DEFAULT`: default workspace persistence when the request omits `persist_workspace`
- `SANDBOX_VOLUME_PREFIX`: prefix used when naming persistent Docker volumes

### 4. Run the API

```bash
uvicorn api_gateway:app --host 0.0.0.0 --port 8088 --reload
```

The API will be available at `http://localhost:8088`.

## API Overview

All endpoints require:

```http
X-API-Key: <your-api-key>
```

### Execute Code

`POST /api/v1/execute`

Example:

```bash
curl -X POST http://localhost:8088/api/v1/execute \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SANDBOX_API_KEY" \
  -d '{
    "type": "python",
    "session_id": "demo-session",
    "persist_workspace": true,
    "install_packages": [],
    "code": "print(\"hello from sandbox\")"
  }'
```

Typical response:

```json
{
  "stdout": "hello from sandbox\n",
  "stderr": "",
  "exit_code": 0,
  "session_id": "demo-session",
  "persist_workspace": true
}
```

### Write a Workspace File

`POST /api/v1/session/{session_id}/files/write`

```bash
curl -X POST http://localhost:8088/api/v1/session/demo-session/files/write \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SANDBOX_API_KEY" \
  -d '{
    "path": "inputs/report.txt",
    "content": "AI Agent Intelligence Report Data",
    "encoding": "utf-8",
    "overwrite": true,
    "persist_workspace": true
  }'
```

### Read a Workspace File

`POST /api/v1/session/{session_id}/files/read`

```bash
curl -X POST http://localhost:8088/api/v1/session/demo-session/files/read \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SANDBOX_API_KEY" \
  -d '{
    "path": "inputs/report.txt",
    "encoding": "utf-8",
    "persist_workspace": true
  }'
```

### List Workspace Files

`POST /api/v1/session/{session_id}/files/list`

```bash
curl -X POST http://localhost:8088/api/v1/session/demo-session/files/list \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SANDBOX_API_KEY" \
  -d '{
    "path": ".",
    "recursive": true,
    "persist_workspace": true
  }'
```

### Purge a Persistent Workspace

`DELETE /api/v1/session/{session_id}`

```bash
curl -X DELETE http://localhost:8088/api/v1/session/demo-session \
  -H "X-API-Key: $SANDBOX_API_KEY"
```

## Workspace Behavior

- When `persist_workspace` is `false`, the engine uses an ephemeral workspace mount.
- When `persist_workspace` is `true`, the engine derives a Docker volume name from the session ID and mounts it at `/workspace`.
- The same `session_id` lets you stage files and then execute code against them later.
- Purging a session removes the persistent Docker volume for that session.

## Open WebUI Integration

This repo includes [`openwebui_sandbox_tools.py`](/hms/apps/sandbox-test/openwebui_sandbox_tools.py:1), which exposes the sandbox API as Open WebUI tools:

- `execute_code`
- `write_workspace_file`
- `read_workspace_file`
- `list_workspace_files`
- `purge_session_workspace`

Relevant tool environment variables:

- `SANDBOX_API_BASE_URL`
- `SANDBOX_API_KEY`
- `SANDBOX_PERSIST_WORKSPACE_DEFAULT`
- `SANDBOX_API_TIMEOUT_SECONDS`

## Testing

Run unit tests:

```bash
python -m unittest discover -s tests
```

Run live integration tests against a running server:

```bash
export SANDBOX_TEST_ENABLE_INTEGRATION=true
export SANDBOX_TEST_API_URL=http://localhost:8088
export SANDBOX_TEST_API_KEY=$SANDBOX_API_KEY
python -m unittest tests.test_integration_api
```

The integration tests cover:

- writing a file
- listing files
- reading a file
- executing code against the staged file
- purging the session workspace

## Known Limitations

- `executor.py` applies a 30 second subprocess timeout, but there is no stronger outer orchestration timeout yet
- `pip install` failures are not surfaced in a very detailed way to the caller
- Execution containers currently allow network access
- Resource controls are present but not fully hardened yet
- Binary file transport is supported, but clients must handle base64 encoding/decoding themselves

## Troubleshooting

- If `docker run --runtime=runsc ...` fails before this app even starts, fix the host gVisor installation first.
- If Docker cannot start containers with `--runtime=runsc`, verify gVisor is installed and Docker knows about the `runsc` runtime.
- If API calls return `403`, check `X-API-Key` and `SANDBOX_API_KEY`.
- If persistent workspace behavior seems inconsistent, confirm `persist_workspace` is being sent explicitly and that `SANDBOX_PERSISTENT_WORKSPACE_DEFAULT` matches your expectation.
- If Open WebUI tool calls fail, verify `SANDBOX_API_BASE_URL` points to this service and the API key matches.

## See Also

- [`README_AI.md`](/hms/apps/sandbox-test/README_AI.md:1) for the original AI-oriented architecture notes
- [`openapi.yaml`](/hms/apps/sandbox-test/openapi.yaml:1) for the machine-readable API specification
- Official gVisor installation guide: https://gvisor.dev/docs/user_guide/install/
- Official gVisor Docker quick start: https://gvisor.dev/docs/user_guide/quick_start/docker/
