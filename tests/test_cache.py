import hashlib
import json

import pytest

from nba_data import cache


def test_publish_and_read_checksummed_cache(tmp_path):
    path = tmp_path / "response.json"

    cache.publish_bytes(path, b'{"ok": true}', {"provider": "test"}, lambda _: None)

    assert cache.read_valid(path, lambda _: None) == b'{"ok": true}'
    metadata = json.loads((tmp_path / "response.json.meta.json").read_text())
    assert metadata["sha256"] == hashlib.sha256(b'{"ok": true}').hexdigest()


def test_changed_cache_fails_without_repair(tmp_path):
    path = tmp_path / "response.json"
    cache.publish_bytes(path, b"original", {}, lambda _: None)
    path.write_bytes(b"changed")

    with pytest.raises(cache.CacheIntegrityError, match="SHA-256"):
        cache.read_valid(path, lambda _: None)


def test_cache_does_not_repair_an_incomplete_pair(tmp_path):
    path = tmp_path / "response.json"
    path.write_bytes(b"orphaned")

    with pytest.raises(cache.CacheIntegrityError, match="incomplete cache pair"):
        cache.require_valid(path, lambda _: None)
