"""env_io — .env merge/write/read semantics (GOTCHAS G-14)."""
from __future__ import annotations

from pathlib import Path

from collector.env_io import (
    apply_to_environ,
    has_keys,
    merge_env,
    read_env,
)


def test_read_env_missing_file_is_empty(tmp_path: Path):
    assert read_env(tmp_path / "nope.env") == {}


def test_merge_env_creates_file(tmp_path: Path):
    p = tmp_path / ".env"
    merge_env(p, {"YOUTUBE_API_KEY": "AIza_yt", "GOOGLE_API_KEY": "AIza_g"})
    env = read_env(p)
    assert env["YOUTUBE_API_KEY"] == "AIza_yt"
    assert env["GOOGLE_API_KEY"] == "AIza_g"


def test_merge_env_preserves_comments_and_other_keys(tmp_path: Path):
    p = tmp_path / ".env"
    p.write_text(
        "# top comment\n"
        "YOUTUBE_API_KEY=여기에_키_붙여넣기\n"
        "\n"
        "# another comment\n"
        "GH_APP_ID=12345\n",
        encoding="utf-8",
    )
    merge_env(p, {"YOUTUBE_API_KEY": "AIza_real", "GOOGLE_API_KEY": "AIza_g"})
    text = p.read_text(encoding="utf-8")
    assert "# top comment" in text
    assert "# another comment" in text
    assert "GH_APP_ID=12345" in text
    env = read_env(p)
    assert env["YOUTUBE_API_KEY"] == "AIza_real"
    assert env["GOOGLE_API_KEY"] == "AIza_g"
    assert env["GH_APP_ID"] == "12345"


def test_merge_env_replaces_in_place(tmp_path: Path):
    p = tmp_path / ".env"
    p.write_text("FOO=old\nBAR=keep\n", encoding="utf-8")
    merge_env(p, {"FOO": "new"})
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "FOO=new"
    assert "BAR=keep" in lines


def test_merge_env_quotes_values_with_spaces(tmp_path: Path):
    p = tmp_path / ".env"
    merge_env(p, {"WEIRD": "hello world"})
    assert '"hello world"' in p.read_text(encoding="utf-8")
    assert read_env(p)["WEIRD"] == "hello world"


def test_has_keys_rejects_placeholders(tmp_path: Path):
    env = {"YOUTUBE_API_KEY": "여기에_키_붙여넣기"}
    assert not has_keys(env, ["YOUTUBE_API_KEY"])
    env["YOUTUBE_API_KEY"] = "AIzaSyRealKey"
    assert has_keys(env, ["YOUTUBE_API_KEY"])


def test_apply_to_environ_sets_vars(monkeypatch):
    monkeypatch.delenv("__TEST_KEY__", raising=False)
    apply_to_environ({"__TEST_KEY__": "hello"})
    import os
    assert os.environ["__TEST_KEY__"] == "hello"


def test_merge_env_empty_updates_is_noop(tmp_path: Path):
    p = tmp_path / ".env"
    p.write_text("FOO=bar\n", encoding="utf-8")
    merge_env(p, {})
    assert p.read_text(encoding="utf-8") == "FOO=bar\n"
