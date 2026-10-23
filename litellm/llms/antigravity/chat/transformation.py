from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final, TypeAlias

import httpx
from pydantic import JsonValue, TypeAdapter

from litellm.llms.base_llm.chat.transformation import BaseConfig, BaseLLMException, LiteLLMLoggingObj
from litellm.llms.vertex_ai.gemini.transformation import (
    gemini_convert_messages_with_history,  # pyright: ignore[reportUnknownVariableType]  # upstream signature
    transform_system_message,
)
from litellm.types.llms.openai import AllMessageValues
from litellm.types.utils import (
    Choices,
    Delta,
    Message,
    ModelResponse,
    ModelResponseStream,
    StreamingChoices,
    Usage,
)

from ..authenticator import Authenticator
from ..common_utils import (
    ANTIGRAVITY_API_BASE,
    DEFAULT_ANTIGRAVITY_USER_AGENT,
    AntigravityAuthError,
    AntigravityError,
    AntigravityQuotaExceededError,
    antigravity_request_envelope,
    default_project_id,
    map_antigravity_model,
)

SUPPORTED_OPENAI_PARAMS: Final[tuple[str, ...]] = (
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "stream",
    "tools",
    "tool_choice",
    "thinking",
    "reasoning_effort",
    "stop",
    "seed",
)

JsonObject: TypeAlias = Mapping[str, JsonValue]
_JSON_OBJECT_ADAPTER: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)


def _optional_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: JsonValue | None) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    return None


class AntigravityConfig(BaseConfig):
    def __init__(self) -> None:
        super().__init__()
        self.authenticator: Final[Authenticator] = Authenticator()

    def get_supported_openai_params(self, model: str) -> list[str]:  # mutable-ok: matches BaseConfig signature
        return list(SUPPORTED_OPENAI_PARAMS)

    def map_openai_params(
        self,
        non_default_params: dict[str, object],  # mutable-ok: matches BaseConfig signature
        optional_params: dict[str, object],  # mutable-ok: matches BaseConfig signature
        model: str,
        drop_params: bool,
    ) -> dict[str, object]:  # mutable-ok: matches BaseConfig signature
        max_tokens: Final[object] = non_default_params.get("max_tokens") or non_default_params.get(
            "max_completion_tokens"
        )
        temp: Final[object] = non_default_params.get("temperature")
        stream_val: Final[object] = non_default_params.get("stream")

        overrides: Final[Mapping[str, object]] = MappingProxyType(
            {
                **({"maxOutputTokens": max_tokens} if max_tokens is not None else {}),
                **({"temperature": temp} if temp is not None else {}),
                **({"stream": stream_val} if stream_val is not None else {}),
            }
        )
        return {**optional_params, **overrides}

    def validate_environment(
        self,
        headers: dict[str, object],  # mutable-ok: matches BaseConfig signature
        model: str,
        messages: list[AllMessageValues],  # mutable-ok: matches BaseConfig signature
        optional_params: dict[str, object],  # mutable-ok: matches BaseConfig signature
        litellm_params: dict[str, object],  # mutable-ok: matches BaseConfig signature
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> dict[str, object]:  # mutable-ok: matches BaseConfig signature
        token: Final[str] = api_key or self.authenticator.get_active_credential()[0]
        base_headers: Final[Mapping[str, str]] = MappingProxyType({str(k): str(v) for k, v in headers.items()})
        auth_headers: Final[Mapping[str, str]] = MappingProxyType(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": base_headers.get("User-Agent") or DEFAULT_ANTIGRAVITY_USER_AGENT,
            }
        )
        return {**base_headers, **auth_headers}

    def get_complete_url(
        self,
        api_base: str | None,
        api_key: str | None,
        model: str,
        optional_params: dict[str, object],  # mutable-ok: matches BaseConfig signature
        litellm_params: dict[str, object],  # mutable-ok: matches BaseConfig signature
        stream: bool | None = None,
    ) -> str:
        base: Final[str] = api_base or ANTIGRAVITY_API_BASE
        suffix: Final[str] = "v1internal:streamGenerateContent?alt=sse" if stream else "v1internal:generateContent"
        return f"{base.rstrip('/')}/{suffix}"

    def transform_request(
        self,
        model: str,
        messages: list[AllMessageValues],  # mutable-ok: matches BaseConfig signature
        optional_params: dict[str, object],  # mutable-ok: matches BaseConfig signature
        litellm_params: dict[str, object],  # mutable-ok: matches BaseConfig signature
        headers: dict[str, object],  # mutable-ok: matches BaseConfig signature
    ) -> dict[str, object]:  # mutable-ok: matches BaseConfig signature
        runtime_model: Final[str] = map_antigravity_model(model)
        project_id: Final[str] = str(
            optional_params.get("vertex_project")
            or optional_params.get("antigravity_project_id")
            or default_project_id()
        )

        sys_tuple: Final = transform_system_message(supports_system_message=True, messages=messages)
        system_instruction_raw: Final[Mapping[str, object] | None] = (
            MappingProxyType(sys_tuple[0]) if isinstance(sys_tuple[0], dict) else None
        )
        cleaned_messages: Final[tuple[AllMessageValues, ...]] = tuple(sys_tuple[1])

        contents: Final = gemini_convert_messages_with_history(  # pyright: ignore[reportUnknownArgumentType]  # list element subtype
            messages=list(cleaned_messages),
            model=runtime_model,
        )

        system_instruction: Final[Mapping[str, object] | None] = (
            MappingProxyType({"role": "user", "parts": system_instruction_raw.get("parts", ())})
            if system_instruction_raw is not None
            else None
        )

        generation_config: Final[Mapping[str, object]] = MappingProxyType(
            {
                **({"temperature": optional_params["temperature"]} if "temperature" in optional_params else {}),
                **(
                    {"maxOutputTokens": optional_params["maxOutputTokens"]}
                    if "maxOutputTokens" in optional_params
                    else {}
                ),
            }
        )

        envelope: Final = antigravity_request_envelope(
            model_id=runtime_model,
            step=max(1, len(contents)),
        )

        tools: Final[object] = optional_params.get("tools")
        inner_request: Final[Mapping[str, object]] = MappingProxyType(
            {
                "contents": contents,
                "sessionId": envelope["session_id"],
                "labels": envelope["labels"],
                **({"systemInstruction": dict(system_instruction)} if system_instruction is not None else {}),
                **({"generationConfig": dict(generation_config)} if generation_config else {}),
                **({"tools": tools} if isinstance(tools, list) and tools else {}),
            }
        )

        return {
            "project": project_id,
            "model": runtime_model,
            "request": dict(inner_request),
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": envelope["request_id"],
        }

    def transform_response(
        self,
        model: str,
        raw_response: httpx.Response,
        model_response: ModelResponse,
        logging_obj: LiteLLMLoggingObj,
        request_data: dict[str, object],  # mutable-ok: matches BaseConfig signature
        messages: list[AllMessageValues],  # mutable-ok: matches BaseConfig signature
        optional_params: dict[str, object],  # mutable-ok: matches BaseConfig signature
        litellm_params: dict[str, object],  # mutable-ok: matches BaseConfig signature
        encoding: object = None,
        api_key: str | None = None,
        json_mode: bool | None = None,
    ) -> ModelResponse:
        status_code: Final[int] = raw_response.status_code
        if status_code == 429:
            auth_header: Final[str] = str(raw_response.request.headers.get("Authorization") or "")
            tried_token: Final[str] = auth_header.removeprefix("Bearer ").strip()
            tried: Final[frozenset[str]] = frozenset({tried_token}) if tried_token else frozenset()
            self.authenticator.failover_to_next_account(tried)
            raise AntigravityQuotaExceededError(
                status_code=429,
                message=f"Antigravity quota exceeded: {raw_response.text}",
            )
        if status_code != 200:
            raise self.get_error_class(raw_response.text, status_code, raw_response.headers)

        parsed: Final[JsonObject] = _JSON_OBJECT_ADAPTER.validate_json(raw_response.text)
        resp_data_val: Final[JsonValue | None] = parsed.get("response")
        resp_data: Final[JsonObject] = resp_data_val if isinstance(resp_data_val, Mapping) else parsed

        candidates_val: Final[JsonValue | None] = resp_data.get("candidates")
        candidates_list: Final[Sequence[JsonValue]] = (
            candidates_val if isinstance(candidates_val, Sequence) else ()
        )
        first_candidate: Final[JsonValue | None] = candidates_list[0] if candidates_list else None
        candidate: Final[JsonObject] = (
            first_candidate if isinstance(first_candidate, Mapping) else MappingProxyType({})
        )

        content_val: Final[JsonValue | None] = candidate.get("content")
        content_obj: Final[JsonObject] = content_val if isinstance(content_val, Mapping) else MappingProxyType({})
        parts_val: Final[JsonValue | None] = content_obj.get("parts")
        parts: Final[Sequence[JsonValue]] = parts_val if isinstance(parts_val, Sequence) else ()

        collected_text: Final[tuple[str, ...]] = tuple(
            text_val
            for part in parts
            if isinstance(part, Mapping)
            and (text_val := _optional_str(part.get("text"))) is not None
            and part.get("thought") is not True
        )
        finish_reason_raw: Final[str | None] = _optional_str(candidate.get("finishReason"))
        finish_reason: Final[str] = (
            "length" if finish_reason_raw == "MAX_TOKENS" else "stop" if finish_reason_raw else "stop"
        )

        message: Final[Message] = Message(
            content="".join(collected_text),
            role="assistant",
        )
        choice: Final[Choices] = Choices(
            finish_reason=finish_reason,
            index=0,
            message=message,
        )
        model_response.choices = [choice]  # rebind-ok: model_response accumulator contract

        usage_meta_val: Final[JsonValue | None] = resp_data.get("usageMetadata")
        if isinstance(usage_meta_val, Mapping):
            prompt_cnt: Final[int] = _optional_int(usage_meta_val.get("promptTokenCount")) or 0
            candidate_cnt: Final[int] = _optional_int(usage_meta_val.get("candidatesTokenCount")) or 0
            total_cnt: Final[int] = (
                _optional_int(usage_meta_val.get("totalTokenCount")) or (prompt_cnt + candidate_cnt)
            )
            setattr(  # noqa: B010  # ModelResponse dynamic usage attribute
                model_response,
                "usage",
                Usage(
                    prompt_tokens=prompt_cnt,
                    completion_tokens=candidate_cnt,
                    total_tokens=total_cnt,
                ),
            )

        return model_response

    def transform_streaming_chunk(
        self,
        raw_chunk: str,
        model: str,
    ) -> ModelResponseStream | None:
        trimmed: Final[str] = raw_chunk.strip()
        if not trimmed or not trimmed.startswith("data:"):
            return None
        payload: Final[str] = trimmed.removeprefix("data:").strip()
        if payload == "[DONE]":
            return ModelResponseStream(
                choices=[StreamingChoices(delta=Delta(), finish_reason="stop", index=0)]
            )

        try:
            chunk_json: Final[JsonObject] = _JSON_OBJECT_ADAPTER.validate_json(payload)
        except ValueError:
            return None

        resp_data_val: Final[JsonValue | None] = chunk_json.get("response")
        resp_data: Final[JsonObject] = resp_data_val if isinstance(resp_data_val, Mapping) else chunk_json
        candidates_val: Final[JsonValue | None] = resp_data.get("candidates")
        candidates_list: Final[Sequence[JsonValue]] = (
            candidates_val if isinstance(candidates_val, Sequence) else ()
        )
        first_candidate: Final[JsonValue | None] = candidates_list[0] if candidates_list else None
        candidate: Final[JsonObject] = (
            first_candidate if isinstance(first_candidate, Mapping) else MappingProxyType({})
        )

        content_val: Final[JsonValue | None] = candidate.get("content")
        content_obj: Final[JsonObject] = content_val if isinstance(content_val, Mapping) else MappingProxyType({})
        parts_val: Final[JsonValue | None] = content_obj.get("parts")
        parts: Final[Sequence[JsonValue]] = parts_val if isinstance(parts_val, Sequence) else ()
        text_parts: Final[tuple[str, ...]] = tuple(
            text_val
            for part in parts
            if isinstance(part, Mapping)
            and (text_val := _optional_str(part.get("text"))) is not None
            and part.get("thought") is not True
        )
        finish_reason_raw: Final[str | None] = _optional_str(candidate.get("finishReason"))
        finish_reason: Final[str | None] = (
            "length" if finish_reason_raw == "MAX_TOKENS" else "stop" if finish_reason_raw else None
        )

        delta_text: Final[str] = "".join(text_parts)
        delta: Final[Delta] = Delta(content=delta_text) if delta_text else Delta()

        return ModelResponseStream(
            choices=[StreamingChoices(delta=delta, finish_reason=finish_reason, index=0)]
        )

    def get_error_class(
        self,
        error_message: str,
        status_code: int,
        headers: dict[str, object] | httpx.Headers,  # mutable-ok: matches BaseConfig signature
    ) -> BaseLLMException:
        if status_code == 429:
            return AntigravityQuotaExceededError(status_code=status_code, message=error_message, headers=headers)
        if status_code in (401, 403):
            return AntigravityAuthError(status_code=status_code, message=error_message, headers=headers)
        return AntigravityError(status_code=status_code, message=error_message, headers=headers)
