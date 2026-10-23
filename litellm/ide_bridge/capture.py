"""Capture records for bridge traffic.

Every proxied request/response pair lands in a JSONL file so an operator can
audit exactly what the IDE sent and where the bridge took it. Headers and
bodies are masked and capped before they hit disk: capture is for wire shape
debugging, not secret harvesting.
"""

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

SECRET_HEADER_NAMES: Final[frozenset[str]] = frozenset(
    {"authorization", "x-api-key", "cookie", "proxy-authorization", "x-goog-api-key"}
)
BODY_PREVIEW_BYTES: Final[int] = 2048


class CapturedRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: float
    method: str
    host: str
    path: str
    upstream: str
    status_code: int
    duration_ms: float
    request_headers: Mapping[str, str]
    request_body_preview: str
    response_body_preview: str


def mask_header(name: str, value: str) -> str:
    if name.lower() not in SECRET_HEADER_NAMES:
        return value
    if len(value) <= 10:
        return "***"
    return f"{value[:10]}***({len(value)} chars)"


def mask_headers(raw_headers: Mapping[str, str]) -> Mapping[str, str]:
    return {name: mask_header(name, value) for name, value in raw_headers.items()}


def body_preview(body: bytes) -> str:
    capped: Final = body[:BODY_PREVIEW_BYTES]
    return capped.decode("utf-8", errors="replace")


class CaptureSink:
    """Appends capture records to a JSONL file, masking every secret first."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(
        self,
        *,
        method: str,
        host: str,
        path: str,
        upstream: str,
        status_code: int,
        duration_ms: float,
        request_headers: Mapping[str, str],
        request_body: bytes,
        response_body: bytes,
        now: float | None = None,
    ) -> CapturedRequest:
        captured: Final[CapturedRequest] = CapturedRequest(
            timestamp=now if now is not None else time.time(),
            method=method,
            host=host,
            path=path,
            upstream=upstream,
            status_code=status_code,
            duration_ms=duration_ms,
            request_headers=mask_headers(request_headers),
            request_body_preview=body_preview(request_body),
            response_body_preview=body_preview(response_body),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as capture_file:
            capture_file.write(captured.model_dump_json() + "\n")
        return captured


def read_captures(path: Path) -> tuple[CapturedRequest, ...]:
    if not path.exists():
        return ()
    lines: Final[tuple[str, ...]] = tuple(path.read_text(encoding="utf-8").splitlines())
    return tuple(CapturedRequest.model_validate_json(line) for line in lines if line.strip())
