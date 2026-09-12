"""Tiny shared helper for spawning subprocesses cross-platform. Every real subprocess spawn in Dev
Studio (git, execute_bash, npm/mvn/gradle test-and-build commands, the live preview dev server)
goes through `asyncio.create_subprocess_exec`, never a shell — so a bare command name like "npm"
has to resolve to the actual executable Windows will run.

Reproduced live on the Windows desktop build: `asyncio.create_subprocess_exec("npm", "test", ...)`
raises `FileNotFoundError: [WinError 2] The system cannot find the file specified` because npm
(like npx/yarn/pnpm/gradlew.bat/mvn.cmd on Windows) is a .cmd/.bat shim, not an .exe — Windows'
CreateProcess only auto-appends .exe to an extension-less name, never the full PATHEXT list a
shell would use. This is the same reason Node's own child_process docs call out `shell: true` as
required for spawning these on Windows without a shell.
"""
from __future__ import annotations

import shutil
from typing import List


def resolve_argv(args: List[str]) -> List[str]:
    """Resolves args[0] to its real, extension-complete path via shutil.which (which IS
    PATHEXT-aware on Windows, unlike a raw CreateProcess call) — a no-op on POSIX, and a no-op
    (falls through to the original string) if the command genuinely isn't found, so the resulting
    FileNotFoundError still names what was actually asked for."""
    if not args:
        return args
    resolved = shutil.which(args[0])
    if resolved:
        return [resolved, *args[1:]]
    return args
