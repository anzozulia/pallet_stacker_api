"""Response schemas. The packing `result` is the core's to_json() dict, passed
through as-is (typed loosely as dict to avoid duplicating the core's schema)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class JobAccepted(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"
    links: Dict[str, str]


class ErrorBody(BaseModel):
    code: str
    message: str
    problems: Optional[List[str]] = None


class JobState(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "failed", "timeout"]
    result: Optional[Dict[str, Any]] = None
    error: Optional[ErrorBody] = None
    meta: Optional[Dict[str, Any]] = None
