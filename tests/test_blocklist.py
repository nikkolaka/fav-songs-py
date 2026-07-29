"""The AI-artist filter. Matching is on Spotify artist id, so a real band that shares a
name with an AI act is never blocked."""

import pytest

from app.aiblocklist import AiBlocklist


@pytest.fixture
def csv_text() -> str:
    padding = "\n".join(f"Filler Act {i},filler{i}" for i in range(600))
    return f"artist,id\nSynthwave Ghost,aiartist-known\nOther AI,aiartist-two\n{padding}\n"


def test_blocks_on_artist_id(tmp_path, csv_text):
    path = tmp_path / "ai.csv"
    path.write_text(csv_text, encoding="utf-8")
    blocklist = AiBlocklist(str(path))

    assert blocklist.blocks_artist_ids(["aiartist-known"]) == "aiartist-known"
    assert blocklist.blocks_artist_ids(["legit-artist"]) is None
    assert blocklist.blocks_artist_ids(["legit-artist", "aiartist-two"]) == "aiartist-two"


def test_name_match_is_case_insensitive_and_splits_collaborations(tmp_path, csv_text):
    path = tmp_path / "ai.csv"
    path.write_text(csv_text, encoding="utf-8")
    blocklist = AiBlocklist(str(path))

    assert blocklist.blocks_artist_name("synthwave ghost") == "synthwave ghost"
    assert blocklist.blocks_artist_name("Real Band, Synthwave Ghost") == "Synthwave Ghost"
    assert blocklist.blocks_artist_name("Chaos In The CBD") is None


def test_unloaded_blocklist_blocks_nothing(tmp_path):
    """Fails open: no list means archive everything, never drop silently."""
    blocklist = AiBlocklist(str(tmp_path / "missing.csv"))

    assert blocklist.loaded is False
    assert blocklist.blocks_artist_ids(["aiartist-known"]) is None
    assert blocklist.blocks_artist_name("Synthwave Ghost") is None


def test_a_truncated_download_cannot_replace_a_good_cache(tmp_path, csv_text, monkeypatch):
    """A GitHub error page must not wipe 7000 artists down to three."""
    path = tmp_path / "ai.csv"
    path.write_text(csv_text, encoding="utf-8")
    blocklist = AiBlocklist(str(path))
    before = len(blocklist._ids)
    assert before > 500

    class Truncated:
        status_code = 200
        text = "artist,id\nOnly One,xyz\n"

        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.aiblocklist.requests.get", lambda *a, **k: Truncated())
    assert blocklist.refresh(force=True) is False
    assert len(blocklist._ids) == before
    assert "suspiciously small" in blocklist.status()["error"]


def test_network_failure_keeps_the_cached_list(tmp_path, csv_text, monkeypatch):
    path = tmp_path / "ai.csv"
    path.write_text(csv_text, encoding="utf-8")
    blocklist = AiBlocklist(str(path))
    before = len(blocklist._ids)

    def boom(*args, **kwargs):
        raise ConnectionError("github unreachable")

    monkeypatch.setattr("app.aiblocklist.requests.get", boom)
    assert blocklist.refresh(force=True) is False
    assert len(blocklist._ids) == before
    assert blocklist.blocks_artist_ids(["aiartist-known"]) == "aiartist-known"
