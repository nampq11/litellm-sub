from typing import Final

from litellm._logging import verbose_proxy_logger
from litellm.caching.caching import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.litellm_core_utils.token_saver.compressor import compress_messages, extract_mappings
from litellm.proxy._types import UserAPIKeyAuth


class PROXY_RTKTokenSaver(CustomLogger):
    def __init__(self) -> None:
        pass

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: dict[str, object],  # mutable-ok: proxy pre-call hook contract
        call_type: str,
    ) -> dict[str, object] | None:  # mutable-ok: proxy pre-call hook contract
        valid_messages: Final = extract_mappings(data.get("messages"))
        if not valid_messages:
            return None

        compressed, stats = compress_messages(valid_messages, enabled=True)
        if stats is not None and stats.bytes_saved > 0:
            filters_used: Final = sorted({h.filter_name for h in stats.hits})
            verbose_proxy_logger.info(
                "[RTK] saved %dB / %dB (%.1f%%) via %s",
                stats.bytes_saved,
                stats.bytes_before,
                stats.percent_saved,
                filters_used,
            )
            updated_data: Final[dict[str, object]] = {  # mutable-ok: proxy pre-call hook return dict
                **data,
                "messages": list(compressed),
            }
            return updated_data

        return None


RTKTokenSaver: Final = PROXY_RTKTokenSaver
_PROXY_RTKTokenSaver: Final = PROXY_RTKTokenSaver
