"""Small subprocess helpers with readable diagnostics."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence


class CommandError(RuntimeError):
    pass


def display_command(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run(
    command: Sequence[str],
    *,
    capture_output: bool = False,
    check: bool = True,
    stdin=None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print(f"+ {display_command(command)}", flush=True)
    result = subprocess.run(
        list(command),
        check=False,
        text=True,
        stdin=stdin,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        env=env,
    )
    if check and result.returncode:
        detail = ""
        if capture_output:
            detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise CommandError(
            f"command failed with exit code {result.returncode}: "
            f"{display_command(command)}{suffix}"
        )
    return result
