from pathlib import Path

from copenguin import SQLiteRuntimeRepository, __version__
from feishu_computer_agent.config import Settings


def test_copenguin_is_public_package_and_default_app_name() -> None:
    settings = Settings.from_env({"COPENGUIN_DATA_DIR": "/tmp/copenguin-test"})

    assert __version__ == "0.1.0"
    assert SQLiteRuntimeRepository is not None
    assert settings.app_name == "CoPenguin"
    assert settings.data_dir == Path("/tmp/copenguin-test")


def test_legacy_data_directory_is_used_when_new_directory_does_not_exist(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    Path(".agent-data").mkdir()

    settings = Settings.from_env({})

    assert settings.data_dir == Path(".agent-data")


def test_new_data_directory_wins_after_copenguin_migration(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path(".agent-data").mkdir()
    Path(".copenguin").mkdir()

    settings = Settings.from_env({})

    assert settings.data_dir == Path(".copenguin")


def test_readmes_keep_repository_owned_penguin_assets() -> None:
    root = Path(__file__).parents[1]
    english = (root / "README.md").read_text(encoding="utf-8")
    chinese = (root / "README.zh.md").read_text(encoding="utf-8")
    png_logo = root / "assets" / "copenguin-logo.png"
    svg_logo = root / "assets" / "copenguin-logo.svg"
    banner = root / "assets" / "readme-banner.svg"

    for readme in (english, chinese):
        assert "assets/copenguin-logo.png" in readme
        assert "assets/copenguin-logo.svg" in readme
        assert "assets/readme-banner.svg" in readme

    assert png_logo.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "<svg" in svg_logo.read_text(encoding="utf-8")
    assert "<svg" in banner.read_text(encoding="utf-8")
