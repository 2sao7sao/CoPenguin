import asyncio

from feishu_computer_agent import computer
from feishu_computer_agent.computer import (
    LocalShellComputerProvider,
    MacOSShortcutsComputerProvider,
)
from feishu_computer_agent.config import Settings
from feishu_computer_agent.models import ComputerTask, RiskLevel


def test_local_shell_provider_requires_explicit_enable() -> None:
    asyncio.run(_test_local_shell_provider_requires_explicit_enable())


async def _test_local_shell_provider_requires_explicit_enable() -> None:
    provider = LocalShellComputerProvider(Settings(local_shell_enabled=False))
    task = ComputerTask(
        instruction="shell: date",
        requester_id="owner",
        chat_id="chat",
        message_id="message",
        risk=RiskLevel.HIGH,
    )

    result = await provider.run(task)

    assert not result.ok
    assert "disabled" in result.summary


def test_local_shell_provider_runs_allowlisted_command() -> None:
    asyncio.run(_test_local_shell_provider_runs_allowlisted_command())


async def _test_local_shell_provider_runs_allowlisted_command() -> None:
    provider = LocalShellComputerProvider(
        Settings(local_shell_enabled=True, local_shell_allowlist=frozenset({"pwd"}))
    )
    task = ComputerTask(
        instruction="shell: pwd",
        requester_id="owner",
        chat_id="chat",
        message_id="message",
        risk=RiskLevel.HIGH,
    )

    result = await provider.run(task)

    assert result.ok
    assert result.summary


def test_macos_shortcuts_provider_requires_explicit_allowlist() -> None:
    asyncio.run(_test_macos_shortcuts_provider_requires_explicit_allowlist())


async def _test_macos_shortcuts_provider_requires_explicit_allowlist() -> None:
    provider = MacOSShortcutsComputerProvider(
        Settings(macos_shortcuts_enabled=True, macos_shortcuts_allowlist=frozenset())
    )
    task = ComputerTask(
        instruction="shortcut: Daily Brief",
        requester_id="owner",
        chat_id="chat",
        message_id="message",
        risk=RiskLevel.HIGH,
    )

    result = await provider.run(task)

    assert not result.ok
    assert "not in MACOS_SHORTCUTS_ALLOWLIST" in result.summary


def test_macos_shortcuts_provider_executes_exact_allowlisted_name(monkeypatch) -> None:
    asyncio.run(_test_macos_shortcuts_provider_executes_exact_allowlisted_name(monkeypatch))


async def _test_macos_shortcuts_provider_executes_exact_allowlisted_name(monkeypatch) -> None:
    calls = []

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"Daily brief created", b""

        def kill(self) -> None:
            raise AssertionError("process should not time out")

        async def wait(self) -> None:
            return None

    async def fake_subprocess(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(computer.sys, "platform", "darwin")
    monkeypatch.setattr(computer.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(computer.asyncio, "create_subprocess_exec", fake_subprocess)
    provider = MacOSShortcutsComputerProvider(
        Settings(
            macos_shortcuts_enabled=True,
            macos_shortcuts_allowlist=frozenset({"Daily Brief"}),
        )
    )
    task = ComputerTask(
        instruction="shortcut: Daily Brief",
        requester_id="owner",
        chat_id="chat",
        message_id="message",
        risk=RiskLevel.HIGH,
    )

    result = await provider.run(task)

    assert result.ok
    assert result.summary == "Daily brief created"
    assert calls[0][0][:3] == ("/usr/bin/shortcuts", "run", "Daily Brief")
