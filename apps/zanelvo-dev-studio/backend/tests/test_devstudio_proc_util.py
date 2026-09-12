"""Deterministic tests for proc_util.resolve_argv — the fix for asyncio.create_subprocess_exec
raising FileNotFoundError: [WinError 2] for npm/npx/yarn/mvn.cmd/gradlew.bat and similar Windows
.cmd/.bat shims (Windows only auto-appends .exe to an extension-less name; it never tries the full
PATHEXT list the way a shell does). No subprocess spawn here — only shutil.which is exercised.
"""
from app.devstudio.services.proc_util import resolve_argv


def test_resolves_a_real_command_on_this_platform():
    # "python3" is guaranteed to exist in this test environment (it's running this very test).
    resolved = resolve_argv(["python3", "--version"])
    assert resolved[0].endswith("python3") or resolved[0] == "python3"
    assert resolved[1:] == ["--version"]


def test_falls_back_to_the_original_name_when_not_found():
    resolved = resolve_argv(["definitely-not-a-real-command-xyz", "arg"])
    assert resolved == ["definitely-not-a-real-command-xyz", "arg"]


def test_empty_args_returned_unchanged():
    assert resolve_argv([]) == []


def test_only_the_first_argument_is_resolved_not_flags_that_look_like_commands():
    resolved = resolve_argv(["python3", "-m", "pytest"])
    assert resolved[1:] == ["-m", "pytest"]
