from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Protocol

from .config import Settings
from .models import ComputerObservation, ComputerTask


class ComputerProvider(Protocol):
    name: str

    async def run(self, task: ComputerTask) -> ComputerObservation: ...


class DryRunComputerProvider:
    name = "dry-run"

    async def run(self, task: ComputerTask) -> ComputerObservation:
        return ComputerObservation(
            ok=True,
            provider=self.name,
            summary=f"Dry-run: would execute computer task: {task.instruction}",
            details={"risk": task.risk.value, "requester_id": task.requester_id},
        )


@dataclass
class LocalShellComputerProvider:
    settings: Settings
    name: str = "local-shell"

    async def run(self, task: ComputerTask) -> ComputerObservation:
        if not self.settings.local_shell_enabled:
            return ComputerObservation(
                ok=False,
                provider=self.name,
                summary="Local shell provider is disabled. Set LOCAL_SHELL_ENABLED=1 to opt in.",
            )
        if not task.instruction.strip().lower().startswith("shell:"):
            return ComputerObservation(
                ok=False,
                provider=self.name,
                summary="Local shell provider only accepts instructions prefixed with `shell:`.",
            )
        command = task.instruction.split(":", 1)[1].strip()
        argv = shlex.split(command)
        if not argv:
            return ComputerObservation(ok=False, provider=self.name, summary="Empty shell command.")
        executable = argv[0]
        if executable not in self.settings.local_shell_allowlist:
            return ComputerObservation(
                ok=False,
                provider=self.name,
                summary=f"Command `{executable}` is not in LOCAL_SHELL_ALLOWLIST.",
                details={"allowlist": sorted(self.settings.local_shell_allowlist)},
            )

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.settings.local_shell_timeout_seconds,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ComputerObservation(
                ok=False,
                provider=self.name,
                summary=f"Command timed out after {self.settings.local_shell_timeout_seconds}s.",
            )

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        return ComputerObservation(
            ok=proc.returncode == 0,
            provider=self.name,
            summary=out or err or f"Command exited with code {proc.returncode}.",
            details={"returncode": proc.returncode, "stderr": err[:4000]},
        )


def build_computer_provider(settings: Settings) -> ComputerProvider:
    if settings.computer_provider == "local-shell":
        return LocalShellComputerProvider(settings)
    return DryRunComputerProvider()
