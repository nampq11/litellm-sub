import json
from unittest.mock import MagicMock

import httpx
import pytest

from litellm.llms.antigravity.chat.transformation import AntigravityConfig
from litellm.llms.antigravity.common_utils import (
    AntigravityQuotaExceededError,
    map_antigravity_model,
)
from litellm.types.utils import ModelResponse


class TestAntigravityTransformation:
    @pytest.fixture
    def config(self):
        return AntigravityConfig()

    def test_map_antigravity_model(self):
        assert map_antigravity_model("antigravity/gemini-3.8-flash") == "gemini-3.8-flash-low"
        assert map_antigravity_model("gemini-3.8-flash") == "gemini-3.8-flash-low"
        assert map_antigravity_model("antigravity/claude-sonnet-4-6") == "claude-sonnet-4-6"
        assert map_antigravity_model("antigravity/claude-opus-4-6") == "claude-opus-4-6-thinking"
        assert map_antigravity_model("antigravity/gpt-oss-120b") == "gpt-oss-120b-medium"
        assert map_antigravity_model("claude-3-7-sonnet") == "claude-sonnet-4-6"

    def test_transform_request_envelope(self, config):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
        optional_params = {
            "antigravity_project_id": "test-project-123",
            "temperature": 0.7,
            "maxOutputTokens": 2048,
        }

        body = config.transform_request(
            model="gemini-3.8-flash",
            messages=messages,
            optional_params=optional_params,
            litellm_params={},
            headers={},
        )

        assert body["project"] == "test-project-123"
        assert body["model"] == "gemini-3.8-flash-low"
        assert body["requestType"] == "agent"
        assert body["userAgent"] == "antigravity"
        assert "requestId" in body
        assert "request" in body

        inner = body["request"]
        assert "contents" in inner
        assert len(inner["contents"]) == 1
        assert inner["contents"][0]["role"] == "user"
        assert inner["contents"][0]["parts"][0]["text"] == "Hello!"
        assert inner["systemInstruction"]["parts"][0]["text"] == "You are a helpful assistant."
        assert inner["generationConfig"]["temperature"] == 0.7
        assert inner["generationConfig"]["maxOutputTokens"] == 2048

    def test_transform_response_success(self, config):
        raw_payload = {
            "response": {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "Hello back!"}],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 4,
                    "totalTokenCount": 14,
                },
            }
        }
        mock_response = httpx.Response(
            status_code=200,
            json=raw_payload,
            request=httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com"),
        )
        model_response = ModelResponse()

        transformed = config.transform_response(
            model="gemini-3.8-flash",
            raw_response=mock_response,
            model_response=model_response,
            logging_obj=MagicMock(),
            request_data={},
            messages=[],
            optional_params={},
            litellm_params={},
        )

        assert len(transformed.choices) == 1
        assert transformed.choices[0].message.content == "Hello back!"
        assert transformed.choices[0].finish_reason == "stop"
        assert transformed.usage.prompt_tokens == 10
        assert transformed.usage.completion_tokens == 4
        assert transformed.usage.total_tokens == 14

    def test_transform_response_429_triggers_quota_error(self, config):
        mock_response = httpx.Response(
            status_code=429,
            text='{"error": {"code": 429, "message": "Quota exceeded"}}',
            request=httpx.Request("POST", "https://daily-cloudcode-pa.googleapis.com"),
        )
        model_response = ModelResponse()

        with pytest.raises(AntigravityQuotaExceededError):
            config.transform_response(
                model="gemini-3.8-flash",
                raw_response=mock_response,
                model_response=model_response,
                logging_obj=MagicMock(),
                request_data={},
                messages=[],
                optional_params={"antigravity_token": "tok-test"},
                litellm_params={},
            )

    def test_transform_streaming_chunk(self, config):
        raw_sse = 'data: {"response": {"candidates": [{"content": {"parts": [{"text": "chunk one"}]}, "finishReason": null}]}}'
        chunk = config.transform_streaming_chunk(raw_sse, model="gemini-3.8-flash")
        assert chunk is not None
        assert chunk.choices[0].delta.content == "chunk one"
        assert chunk.choices[0].finish_reason is None

        done_sse = "data: [DONE]"
        done_chunk = config.transform_streaming_chunk(done_sse, model="gemini-3.8-flash")
        assert done_chunk is not None
        assert done_chunk.choices[0].finish_reason == "stop"
