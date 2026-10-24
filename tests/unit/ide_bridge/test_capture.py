import json
from pathlib import Path
from typing import Final

from litellm.ide_bridge.capture import (
    CaptureSink,
    body_preview,
    mask_header,
    mask_headers,
    read_captures,
)

CAPTURE_NOW: Final = 1735689600.0


class TestMaskHeader:
    def test_secret_headers_are_masked(self):
        masked = mask_header("authorization", "Bearer sk-ant-very-secret-value")

        assert masked.startswith("Bearer sk-")
        assert "very-secret" not in masked
        assert masked.endswith("chars)")

    def test_short_secrets_are_fully_hidden(self):
        assert mask_header("x-api-key", "short") == "***"

    def test_non_secret_headers_pass_through(self):
        assert mask_header("content-type", "application/json") == "application/json"

    def test_matching_is_case_insensitive(self):
        masked = mask_header("X-Api-Key", "secret-value-12345")

        assert "12345" not in masked


class TestMaskHeaders:
    def test_only_secret_names_are_masked(self):
        masked = mask_headers({"authorization": "Bearer abcdefghijklm", "accept": "application/json"})

        assert masked == {"authorization": "Bearer abc***(20 chars)", "accept": "application/json"}


class TestBodyPreview:
    def test_preview_is_capped(self):
        preview = body_preview(b"x" * 5000)

        assert len(preview) == 2048

    def test_non_utf8_bytes_are_replaced_not_raised(self):
        assert body_preview(b"\xff\xfeok") == "\ufffd\ufffdok"


class TestCaptureSink:
    def test_record_appends_masked_jsonl(self, tmp_path: Path):
        sink = CaptureSink(tmp_path / "capture.jsonl")

        sink.record(
            method="POST",
            host="api.anthropic.com",
            path="/v1/messages",
            upstream="gateway",
            status_code=200,
            duration_ms=12.5,
            request_headers={"authorization": "Bearer abcdefghijklm"},
            request_body=b'{"model": "claude-sonnet-4-6"}',
            response_body=b'{"id": "msg_1"}',
            now=CAPTURE_NOW,
        )
        sink.record(
            method="GET",
            host="cloudcode-pa.googleapis.com",
            path="/health",
            upstream="passthrough",
            status_code=204,
            duration_ms=3.0,
            request_headers={},
            request_body=b"",
            response_body=b"",
            now=CAPTURE_NOW + 1,
        )

        lines = (tmp_path / "capture.jsonl").read_text().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["timestamp"] == CAPTURE_NOW
        assert first["status_code"] == 200
        assert "Bearer abc" in first["request_headers"]["authorization"]
        assert "abcdefghijklm" not in first["request_headers"]["authorization"]
        assert first["request_body_preview"] == '{"model": "claude-sonnet-4-6"}'

    def test_records_round_trip_through_read_captures(self, tmp_path: Path):
        path = tmp_path / "capture.jsonl"
        CaptureSink(path).record(
            method="POST",
            host="api.anthropic.com",
            path="/v1/messages",
            upstream="gateway",
            status_code=200,
            duration_ms=1.0,
            request_headers={"x-api-key": "sk-secret-secret"},
            request_body=b"req",
            response_body=b"resp",
            now=CAPTURE_NOW,
        )

        records = read_captures(path)

        assert len(records) == 1
        assert records[0].host == "api.anthropic.com"
        assert records[0].upstream == "gateway"
        assert records[0].request_headers["x-api-key"].endswith("chars)")

    def test_read_captures_of_missing_file_is_empty(self, tmp_path: Path):
        assert read_captures(tmp_path / "nothing.jsonl") == ()
