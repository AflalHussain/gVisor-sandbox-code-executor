from typing import List, Optional

from pydantic import BaseModel, Field


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
