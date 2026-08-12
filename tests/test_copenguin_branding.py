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
