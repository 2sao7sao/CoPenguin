from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _csv_set(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    app_name: str = "CoPenguin"
    host: str = "127.0.0.1"
    port: int = 8787
    data_dir: Path = Path(".copenguin")
    default_project_id: str = "personal"

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_bot_open_id: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_allowed_open_ids: frozenset[str] = field(default_factory=frozenset)
    feishu_allowed_union_ids: frozenset[str] = field(default_factory=frozenset)
    trust_all_feishu_users_for_dev: bool = False
    require_group_mention: bool = True

    approval_required: bool = True
    approval_ttl_seconds: int = 1800

    computer_provider: str = "dry-run"
    local_shell_enabled: bool = False
    local_shell_allowlist: frozenset[str] = field(
        default_factory=lambda: frozenset({"pwd", "ls", "date", "whoami"})
    )
    local_shell_timeout_seconds: int = 20

    worker_concurrency: int = 1
    worker_lease_seconds: int = 30
    worker_heartbeat_interval_seconds: float = 5.0
    worker_retry_delay_seconds: int = 1

    memory_enabled: bool = True
    knowledge_enabled: bool = True
    kb_root: Path = Path("kb")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        configured_data_dir = env.get("COPENGUIN_DATA_DIR") or env.get("AGENT_DATA_DIR")
        if configured_data_dir:
            data_dir = Path(configured_data_dir).expanduser()
        else:
            new_default = Path(".copenguin")
            legacy_default = Path(".agent-data")
            data_dir = (
                legacy_default
                if legacy_default.exists() and not new_default.exists()
                else new_default
            )
        return cls(
            host=env.get("HOST", "127.0.0.1"),
            port=int(env.get("PORT", "8787")),
            data_dir=data_dir,
            default_project_id=env.get("COPENGUIN_DEFAULT_PROJECT_ID", "personal"),
            feishu_app_id=env.get("FEISHU_APP_ID", ""),
            feishu_app_secret=env.get("FEISHU_APP_SECRET", ""),
            feishu_bot_open_id=env.get("FEISHU_BOT_OPEN_ID", ""),
            feishu_verification_token=env.get("FEISHU_VERIFICATION_TOKEN", ""),
            feishu_encrypt_key=env.get("FEISHU_ENCRYPT_KEY", ""),
            feishu_allowed_open_ids=_csv_set(env.get("FEISHU_ALLOWED_OPEN_IDS")),
            feishu_allowed_union_ids=_csv_set(env.get("FEISHU_ALLOWED_UNION_IDS")),
            trust_all_feishu_users_for_dev=_bool(env.get("TRUST_ALL_FEISHU_USERS_FOR_DEV"), False),
            require_group_mention=_bool(env.get("FEISHU_REQUIRE_GROUP_MENTION"), True),
            approval_required=_bool(env.get("APPROVAL_REQUIRED"), True),
            approval_ttl_seconds=int(env.get("APPROVAL_TTL_SECONDS", "1800")),
            computer_provider=env.get("COMPUTER_PROVIDER", "dry-run"),
            local_shell_enabled=_bool(env.get("LOCAL_SHELL_ENABLED"), False),
            local_shell_allowlist=_csv_set(env.get("LOCAL_SHELL_ALLOWLIST"))
            or frozenset({"pwd", "ls", "date", "whoami"}),
            local_shell_timeout_seconds=int(env.get("LOCAL_SHELL_TIMEOUT_SECONDS", "20")),
            worker_concurrency=int(env.get("COPENGUIN_WORKER_CONCURRENCY", "1")),
            worker_lease_seconds=int(env.get("COPENGUIN_WORKER_LEASE_SECONDS", "30")),
            worker_heartbeat_interval_seconds=float(
                env.get("COPENGUIN_WORKER_HEARTBEAT_SECONDS", "5")
            ),
            worker_retry_delay_seconds=int(env.get("COPENGUIN_WORKER_RETRY_DELAY_SECONDS", "1")),
            memory_enabled=_bool(env.get("MEMORY_ENABLED"), True),
            knowledge_enabled=_bool(env.get("KNOWLEDGE_ENABLED"), True),
            kb_root=Path(env.get("KB_ROOT", "kb")).expanduser(),
        )

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"

    @property
    def approval_dir(self) -> Path:
        return self.data_dir / "approvals"

    @property
    def runtime_database(self) -> Path:
        return self.data_dir / "runtime" / "runtime.db"

    @property
    def artifact_dir(self) -> Path:
        return self.data_dir / "runtime" / "artifacts"


def load_settings() -> Settings:
    return Settings.from_env()
