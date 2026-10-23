from litellm.litellm_core_utils.token_saver.compressor import compress_messages, compress_text


def test_compress_text_small_untouched():
    small_diff = "diff --git a/foo.py b/foo.py\n+hello world\n"
    out, hit = compress_text(small_diff, "openai-tool")
    assert hit is None
    assert out == small_diff


def test_compress_text_large_diff():
    added = "\n".join(f"+line_{i} = {i}" for i in range(120))
    large_diff = f"diff --git a/test.py b/test.py\n@@ -1,1 +1,120 @@\n{added}\n"
    assert len(large_diff) >= 500
    out, hit = compress_text(large_diff, "openai-tool")
    assert hit is not None
    assert hit.filter_name == "git-diff"
    assert hit.saved_bytes > 0
    assert len(out) < len(large_diff)


def test_compress_messages_openai_tool():
    added = "\n".join(f"+line_{i} = {i}" for i in range(120))
    large_diff = f"diff --git a/test.py b/test.py\n@@ -1,1 +1,120 @@\n{added}\n"

    messages = [
        {"role": "user", "content": "run git diff"},
        {"role": "assistant", "content": "Here is the diff"},
        {"role": "tool", "content": large_diff},
    ]

    compressed, stats = compress_messages(messages, enabled=True)
    assert stats is not None
    assert stats.bytes_saved > 0
    assert stats.percent_saved > 0
    assert len(compressed) == 3
    assert len(str(compressed[2]["content"])) < len(large_diff)
    assert compressed[0]["content"] == "run git diff"


def test_compress_messages_anthropic_tool_result():
    added = "\n".join(f"+line_{i} = {i}" for i in range(120))
    large_diff = f"diff --git a/test.py b/test.py\n@@ -1,1 +1,120 @@\n{added}\n"

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_123",
                    "content": large_diff,
                }
            ],
        }
    ]

    compressed, stats = compress_messages(messages, enabled=True)
    assert stats is not None
    assert stats.bytes_saved > 0
    assert len(compressed) == 1
    blocks = compressed[0]["content"]
    assert isinstance(blocks, (list, tuple))
    assert len(str(blocks[0]["content"])) < len(large_diff)


def test_compress_messages_preserves_error_traces():
    error_output = (
        "Traceback (most recent call last):\n"
        + "\n".join(f'  File "test_{i}.py", line {i}, in <module>' for i in range(100))
        + "\nZeroDivisionError: division by zero"
    )

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_err",
                    "is_error": True,
                    "content": error_output,
                }
            ],
        }
    ]

    compressed, stats = compress_messages(messages, enabled=True)
    assert stats is None
    blocks = compressed[0]["content"]
    assert isinstance(blocks, (list, tuple))
    assert blocks[0]["content"] == error_output


def test_compress_messages_function_call_output():
    added = "\n".join(f"+line_{i} = {i}" for i in range(120))
    large_diff = f"diff --git a/test.py b/test.py\n@@ -1,1 +1,120 @@\n{added}\n"

    messages = [
        {"type": "function_call_output", "call_id": "call_1", "output": large_diff}
    ]

    compressed, stats = compress_messages(messages, enabled=True)
    assert stats is not None
    assert stats.bytes_saved > 0
    assert len(str(compressed[0]["output"])) < len(large_diff)
