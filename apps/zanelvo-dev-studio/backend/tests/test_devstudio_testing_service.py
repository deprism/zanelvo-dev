"""Regression tests for TestingService, specifically the cwd/command separation.

Caught by live end-to-end testing (not just unit tests): TestingService used to bake `cd <dir> &&`
into command strings. That fails two different ways — CommandPolicy rejects it (`cd` isn't on the
allowlist) and, even if it were, ExecutionService runs commands via `create_subprocess_exec`, which
never invokes a shell and can't interpret `&&` at all. `cwd` must always be a separate field.
"""
import json
import os

from app.devstudio.services import command_policy, testing_service


def _write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)


def test_flat_repo_layout_backend_and_frontend_at_root(tmp_path):
    _write(tmp_path / "backend" / "requirements.txt")
    _write(tmp_path / "backend" / "pytest.ini")
    _write(tmp_path / "backend" / "ruff.toml")
    _write(tmp_path / "frontend" / "package.json", json.dumps({"scripts": {"test": "x", "build": "x"}}))

    profile = testing_service.discover(str(tmp_path))
    assert profile.has_backend and profile.backend_dir == "backend"
    assert profile.has_frontend and profile.frontend_dir == "frontend"


def test_nested_monorepo_layout_is_discovered(tmp_path):
    _write(tmp_path / "apps" / "myapp" / "backend" / "requirements.txt")
    _write(tmp_path / "apps" / "myapp" / "frontend" / "package.json",
           json.dumps({"scripts": {"build": "x"}}))

    profile = testing_service.discover(str(tmp_path))
    assert profile.has_backend and profile.backend_dir == "apps/myapp/backend"
    assert profile.has_frontend and profile.frontend_dir == "apps/myapp/frontend"


def test_selected_commands_never_contain_cd_or_shell_operators(tmp_path):
    _write(tmp_path / "apps" / "myapp" / "backend" / "requirements.txt")
    _write(tmp_path / "apps" / "myapp" / "backend" / "ruff.toml")
    _write(tmp_path / "apps" / "myapp" / "frontend" / "package.json",
           json.dumps({"scripts": {"test": "x", "build": "x", "lint": "x"}}))

    profile = testing_service.discover(str(tmp_path))
    commands = testing_service.select_commands(profile, changed_files=[])
    assert commands, "expected at least one command to be selected"
    for c in commands:
        assert "cd " not in c["command"]
        assert "&&" not in c["command"]
        assert ";" not in c["command"]


def test_every_selected_command_passes_command_policy(tmp_path):
    _write(tmp_path / "backend" / "requirements.txt")
    _write(tmp_path / "backend" / "ruff.toml")
    _write(tmp_path / "frontend" / "package.json",
           json.dumps({"scripts": {"test": "x", "build": "x", "lint": "x"}}))

    profile = testing_service.discover(str(tmp_path))
    commands = testing_service.select_commands(profile, changed_files=[])
    for c in commands:
        decision = command_policy.evaluate(c["command"])
        assert decision.allowed, f"{c['command']!r} should be allowed: {decision.reason}"


def test_maven_project_is_discovered_and_uses_the_wrapper_when_present(tmp_path):
    _write(tmp_path / "plugin" / "pom.xml", "<project></project>")
    _write(tmp_path / "plugin" / "mvnw", "#!/bin/sh\n")

    profile = testing_service.discover(str(tmp_path))
    assert profile.has_java and profile.java_dir == "plugin"
    assert profile.java_build_cmd == "./mvnw -B package"
    assert profile.java_test_cmd == "./mvnw -B test"


def test_maven_project_without_a_wrapper_falls_back_to_system_mvn(tmp_path):
    _write(tmp_path / "pom.xml", "<project></project>")

    profile = testing_service.discover(str(tmp_path))
    assert profile.has_java and profile.java_dir == ""
    assert profile.java_build_cmd == "mvn -B package"


def test_gradle_project_is_discovered_and_uses_the_wrapper_when_present(tmp_path):
    _write(tmp_path / "build.gradle.kts", "")
    _write(tmp_path / "gradlew", "#!/bin/sh\n")

    profile = testing_service.discover(str(tmp_path))
    assert profile.has_java
    assert profile.java_build_cmd == "./gradlew build"
    assert profile.java_test_cmd == "./gradlew test"


def test_java_build_and_test_commands_are_selected_and_pass_command_policy(tmp_path):
    _write(tmp_path / "pom.xml", "<project></project>")

    profile = testing_service.discover(str(tmp_path))
    commands = testing_service.select_commands(profile, changed_files=[])
    types = {c["type"] for c in commands}
    assert {"java_unit", "build"}.issubset(types)
    for c in commands:
        decision = command_policy.evaluate(c["command"])
        assert decision.allowed, f"{c['command']!r} should be allowed: {decision.reason}"


def test_every_selected_command_type_is_a_valid_test_run_model_field(tmp_path):
    # A real bug this caught: select_commands started emitting "java_unit" as a type, but
    # TestRun.test_type (models.py) is a fixed Literal that didn't include it — every Java test/
    # build run would have raised a pydantic ValidationError the instant qa_agent tried to record
    # it, invisible to command_policy-only checks like the test above.
    from app.devstudio.models import TestRun

    allowed_types = TestRun.model_fields["test_type"].annotation.__args__
    _write(tmp_path / "backend" / "requirements.txt")
    _write(tmp_path / "backend" / "ruff.toml")
    _write(tmp_path / "frontend" / "package.json",
           json.dumps({"scripts": {"test": "x", "build": "x", "lint": "x"}}))
    _write(tmp_path / "pom.xml", "<project></project>")

    profile = testing_service.discover(str(tmp_path))
    commands = testing_service.select_commands(profile, changed_files=[])
    assert commands, "expected at least one command"
    for c in commands:
        assert c["type"] in allowed_types, f"{c['type']!r} is not a valid TestRun.test_type"


def test_cwd_is_reported_separately_from_command(tmp_path):
    _write(tmp_path / "apps" / "myapp" / "backend" / "requirements.txt")

    profile = testing_service.discover(str(tmp_path))
    commands = testing_service.select_commands(profile, changed_files=[])
    backend_cmd = next(c for c in commands if c["type"] == "backend_unit")
    assert backend_cmd["command"] == "python -m pytest -q"
    assert backend_cmd["cwd"] == "apps/myapp/backend"
