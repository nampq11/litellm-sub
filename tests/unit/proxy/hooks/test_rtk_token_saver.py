import pytest

from litellm.caching.caching import DualCache
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.hooks.rtk_token_saver import PROXY_RTKTokenSaver


@pytest.mark.asyncio
async def test_rtk_token_saver_hook_compresses_tool_messages():
    saver = PROXY_RTKTokenSaver()
    added = "\n".join(f"+var_{i} = {i}" for i in range(120))
    large_diff = f"diff --git a/app.py b/app.py\n@@ -1,1 +1,120 @@\n{added}\n"

    req_data = {
        "model": "gpt-4",
        "messages": [
            {"role": "user", "content": "check diff"},
            {"role": "tool", "content": large_diff},
        ],
    }

    result = await saver.async_pre_call_hook(
        user_api_key_dict=UserAPIKeyAuth(),
        cache=DualCache(),
        data=req_data,
        call_type="completion",
    )

    assert result is not None
    messages = result["messages"]
    assert len(messages) == 2
    assert len(str(messages[1]["content"])) < len(large_diff)


@pytest.mark.asyncio
async def test_rtk_token_saver_hook_noop_on_ordinary_text():
    saver = PROXY_RTKTokenSaver()
    req_data = {
        "model": "gpt-4",
        "messages": [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there"},
        ],
    }

    result = await saver.async_pre_call_hook(
        user_api_key_dict=UserAPIKeyAuth(),
        cache=DualCache(),
        data=req_data,
        call_type="completion",
    )

    assert result is None
