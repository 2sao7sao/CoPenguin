import asyncio

from feishu_computer_agent.computer import LocalShellComputerProvider
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
