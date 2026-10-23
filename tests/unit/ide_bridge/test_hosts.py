from pathlib import Path

from litellm.ide_bridge.hosts import (
    MARKER_TAG,
    apply_hosts_block,
    hosts_block,
    merge_hosts_block,
    remove_hosts_block,
    strip_hosts_block,
)

PRE_EXISTING: str = "# system hosts\n127.0.0.1 localhost\n::1 localhost\n\n10.0.0.5 buildserver\n"


class TestHostsBlock:
    def test_block_is_fenced_and_contains_each_host(self):
        block = hosts_block(MARKER_TAG, "127.0.0.1", ("api.anthropic.com", "api.openai.com"))

        assert block.splitlines()[0] == "# BEGIN litellm-ide-bridge"
        assert block.splitlines()[-1] == "# END litellm-ide-bridge"
        assert "127.0.0.1 api.anthropic.com" in block
        assert "127.0.0.1 api.openai.com" in block


class TestMergeHostsBlock:
    def test_merge_into_fresh_file_preserves_existing_content(self):
        merged = merge_hosts_block(
            PRE_EXISTING, hosts_block(MARKER_TAG, "127.0.0.1", ("api.anthropic.com",)), MARKER_TAG
        )

        assert merged.startswith("# system hosts\n127.0.0.1 localhost")
        assert "10.0.0.5 buildserver" in merged
        assert "127.0.0.1 api.anthropic.com" in merged

    def test_applying_twice_replaces_instead_of_duplicating(self):
        once = merge_hosts_block(PRE_EXISTING, hosts_block(MARKER_TAG, "127.0.0.1", ("api.anthropic.com",)), MARKER_TAG)
        twice = merge_hosts_block(once, hosts_block(MARKER_TAG, "127.0.0.1", ("api.anthropic.com",)), MARKER_TAG)

        assert twice == once
        assert twice.count("api.anthropic.com") == 1

    def test_stale_hosts_are_dropped_on_reapply(self):
        once = merge_hosts_block(PRE_EXISTING, hosts_block(MARKER_TAG, "127.0.0.1", ("old.example.com",)), MARKER_TAG)
        updated = merge_hosts_block(once, hosts_block(MARKER_TAG, "127.0.0.1", ("new.example.com",)), MARKER_TAG)

        assert "old.example.com" not in updated
        assert "new.example.com" in updated


class TestStripHostsBlock:
    def test_strip_restores_the_original_text(self):
        merged = merge_hosts_block(
            PRE_EXISTING, hosts_block(MARKER_TAG, "127.0.0.1", ("api.anthropic.com",)), MARKER_TAG
        )

        stripped = strip_hosts_block(merged, MARKER_TAG)

        assert stripped == PRE_EXISTING

    def test_text_without_a_block_is_returned_untouched(self):
        assert strip_hosts_block(PRE_EXISTING, MARKER_TAG) == PRE_EXISTING

    def test_missing_end_marker_truncates_from_the_begin_marker(self):
        truncated = PRE_EXISTING + "# BEGIN litellm-ide-bridge\n127.0.0.1 api.anthropic.com\n"

        assert strip_hosts_block(truncated, MARKER_TAG) == PRE_EXISTING


class TestFileOperations:
    def test_apply_then_remove_round_trips_the_hosts_file(self, tmp_path: Path):
        hosts_file = tmp_path / "hosts"
        hosts_file.write_text(PRE_EXISTING)

        apply_hosts_block(hosts_file, MARKER_TAG, "127.0.0.1", ("api.anthropic.com",))
        assert "api.anthropic.com" in hosts_file.read_text()

        remove_hosts_block(hosts_file, MARKER_TAG)
        assert hosts_file.read_text() == PRE_EXISTING

    def test_remove_on_a_missing_file_is_a_no_op(self, tmp_path: Path):
        missing = tmp_path / "missing"

        remove_hosts_block(missing, MARKER_TAG)

        assert not missing.exists()
