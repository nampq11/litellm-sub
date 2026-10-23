from collections.abc import Mapping, Sequence
from typing import Final

from litellm.litellm_core_utils.token_saver.autodetect import auto_detect_filter
from litellm.litellm_core_utils.token_saver.constants import MIN_COMPRESS_SIZE, RAW_CAP
from litellm.litellm_core_utils.token_saver.types import FilterHit, TokenSaverStats


def extract_mappings(items: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(items, (list, tuple)):
        return ()
    out: Final[list[Mapping[str, object]]] = []  # mutable-ok: builder
    for item in items:  # pyright: ignore[reportUnknownVariableType]  # boundary check on json payload
        if isinstance(item, Mapping):
            out.append({str(k): v for k, v in item.items()})  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]  # boundary dict copy
    return tuple(out)


def compress_text(text: str, shape: str) -> tuple[str, FilterHit | None]:
    text_len: Final = len(text)
    if text_len < MIN_COMPRESS_SIZE or text_len > RAW_CAP:
        return text, None

    detected: Final = auto_detect_filter(text)
    if detected is None:
        return text, None

    filter_fn, filter_name = detected
    try:
        compressed: Final = filter_fn(text)
    except Exception:  # noqa: BLE001  # fail-open contract to protect request pipeline
        return text, None

    if not compressed or len(compressed) >= text_len:
        return text, None

    saved_bytes: Final = text_len - len(compressed)
    hit: Final = FilterHit(shape=shape, filter_name=filter_name, saved_bytes=saved_bytes)
    return compressed, hit


def _compress_anthropic_text_parts(
    parts: Sequence[Mapping[str, object]],
    hits: list[FilterHit],  # mutable-ok: accumulator
) -> tuple[tuple[Mapping[str, object], ...], int, int]:
    new_inner: Final[list[Mapping[str, object]]] = []  # mutable-ok: builder for inner parts
    b_in: int = 0  # rebind-ok: byte count
    b_out: int = 0  # rebind-ok: byte count

    for part in parts:
        part_type = part.get("type")
        raw_text = part.get("text")
        if part_type == "text" and isinstance(raw_text, str):
            b_in += len(raw_text)
            comp_part, part_hit = compress_text(raw_text, "claude-array")
            b_out += len(comp_part)
            if part_hit is not None:
                hits.append(part_hit)
            part_dict = dict(part)
            part_dict["text"] = comp_part
            new_inner.append(part_dict)
        else:
            new_inner.append(dict(part))

    return tuple(new_inner), b_in, b_out


def _compress_anthropic_block(
    block: Mapping[str, object],
    hits: list[FilterHit],  # mutable-ok: accumulator
) -> tuple[Mapping[str, object], int, int]:
    block_dict: Final[dict[str, object]] = dict(block)  # mutable-ok: clone block for compression
    if block_dict.get("type") != "tool_result" or block_dict.get("is_error") is True or block_dict.get("status") == "error":
        return block_dict, 0, 0

    inner: Final = block_dict.get("content")
    if isinstance(inner, str):
        comp, hit = compress_text(inner, "claude-string")
        if hit is not None:
            hits.append(hit)
        block_dict["content"] = comp
        return block_dict, len(inner), len(comp)

    valid_parts: Final = extract_mappings(inner)
    if valid_parts:
        new_parts, b_in, b_out = _compress_anthropic_text_parts(valid_parts, hits)
        block_dict["content"] = new_parts
        return block_dict, b_in, b_out

    return block_dict, 0, 0


def _compress_anthropic_content_blocks(
    content: Sequence[Mapping[str, object]],
) -> tuple[tuple[Mapping[str, object], ...], tuple[FilterHit, ...], int, int]:
    new_blocks: Final[list[Mapping[str, object]]] = []  # mutable-ok: builder for content blocks
    hits: Final[list[FilterHit]] = []  # mutable-ok: builder for hits
    bytes_in: int = 0  # rebind-ok: byte accumulator
    bytes_out: int = 0  # rebind-ok: byte accumulator

    for block in content:
        comp_block, b_in, b_out = _compress_anthropic_block(block, hits)
        new_blocks.append(comp_block)
        bytes_in += b_in
        bytes_out += b_out

    return tuple(new_blocks), tuple(hits), bytes_in, bytes_out


def _compress_openai_tool_parts(
    content: Sequence[Mapping[str, object]],
    hits_acc: list[FilterHit],  # mutable-ok: accumulator
) -> tuple[tuple[Mapping[str, object], ...], int, int]:
    new_parts: Final[list[Mapping[str, object]]] = []  # mutable-ok: builder for parts
    b_in: int = 0  # rebind-ok: byte count
    b_out: int = 0  # rebind-ok: byte count

    for part in content:
        part_type = part.get("type")
        raw_text = part.get("text")
        if part_type == "text" and isinstance(raw_text, str):
            b_in += len(raw_text)
            comp_text, part_hit = compress_text(raw_text, "openai-tool-array")
            b_out += len(comp_text)
            if part_hit is not None:
                hits_acc.append(part_hit)
            p_dict = dict(part)
            p_dict["text"] = comp_text
            new_parts.append(p_dict)
        else:
            new_parts.append(dict(part))

    return tuple(new_parts), b_in, b_out


def _compress_single_message(
    msg: Mapping[str, object],
    hits_acc: list[FilterHit],  # mutable-ok: accumulator
) -> tuple[Mapping[str, object], int, int]:
    msg_dict: Final[dict[str, object]] = dict(msg)  # mutable-ok: clone message dict
    role: Final = msg_dict.get("role")
    msg_type: Final = msg_dict.get("type")

    if msg_type == "function_call_output":
        raw_output: Final = msg_dict.get("output")
        if isinstance(raw_output, str):
            resp_comp, resp_hit = compress_text(raw_output, "openai-responses-string")
            if resp_hit is not None:
                hits_acc.append(resp_hit)
            msg_dict["output"] = resp_comp
            return msg_dict, len(raw_output), len(resp_comp)
        return msg_dict, 0, 0

    if role == "tool":
        content: Final = msg_dict.get("content")
        if isinstance(content, str):
            tool_comp, tool_hit = compress_text(content, "openai-tool")
            if tool_hit is not None:
                hits_acc.append(tool_hit)
            msg_dict["content"] = tool_comp
            return msg_dict, len(content), len(tool_comp)
        tool_valid_parts: Final = extract_mappings(content)
        if tool_valid_parts:
            tool_parts, tool_b_in, tool_b_out = _compress_openai_tool_parts(tool_valid_parts, hits_acc)
            msg_dict["content"] = tool_parts
            return msg_dict, tool_b_in, tool_b_out
        return msg_dict, 0, 0

    content_val: Final = msg_dict.get("content")
    valid_blocks: Final = extract_mappings(content_val)
    if valid_blocks:
        comp_blocks, b_hits, ant_b_in, ant_b_out = _compress_anthropic_content_blocks(valid_blocks)
        if ant_b_in > 0:
            hits_acc.extend(b_hits)
            msg_dict["content"] = comp_blocks
            return msg_dict, ant_b_in, ant_b_out

    return msg_dict, 0, 0


def compress_messages(
    messages: Sequence[Mapping[str, object]],
    enabled: bool = True,
) -> tuple[tuple[Mapping[str, object], ...], TokenSaverStats | None]:
    if not enabled or not messages:
        return (), None

    hits_acc: Final[list[FilterHit]] = []  # mutable-ok: accumulator for filter hits
    new_messages: Final[list[Mapping[str, object]]] = []  # mutable-ok: builder for transformed messages
    total_in: int = 0  # rebind-ok: total bytes accumulator
    total_out: int = 0  # rebind-ok: total bytes accumulator

    for msg in messages:
        comp_msg, b_in, b_out = _compress_single_message(msg, hits_acc)
        total_in += b_in
        total_out += b_out
        new_messages.append(comp_msg)

    if not hits_acc:
        return tuple(new_messages), None

    stats: Final = TokenSaverStats(
        bytes_before=total_in,
        bytes_after=total_out,
        hits=tuple(hits_acc),
    )
    return tuple(new_messages), stats
