"""TestingService — discovers what test/build/lint commands a repository actually supports,
and picks a risk-appropriate subset based on which files changed (never "always run everything").

Every discovered command is a plain, directly-executable command (no shell operators) plus a
separate `cwd` (relative subdirectory to run it in). This matters: ExecutionService runs commands
via `asyncio.create_subprocess_exec`, which does NOT go through a shell — a command string like
"cd backend && pytest" would neither pass CommandPolicy (its first token "cd" isn't executable)
nor run correctly even if it did (`create_subprocess_exec` can't interpret `&&`). Keeping `cwd`
separate from `command` is what makes commands both policy-checkable and actually runnable.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

_IS_WINDOWS = sys.platform.startswith("win")

_ROUTE_HINTS = {
    "auth": ["auth", "login", "session", "jwt", "permission", "role"],
    "payment": ["stripe", "payment", "billing", "webhook", "checkout", "invoice"],
    "frontend": ["frontend/src", ".jsx", ".tsx", "frontend/"],
    "backend": ["backend/app", ".py"],
    "java": ["pom.xml", "build.gradle", ".java", ".kt", "src/main/java"],
}

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}


@dataclass
class TestingProfile:
    has_frontend: bool = False
    has_backend: bool = False
    frontend_dir: Optional[str] = None   # relative to workspace root; "" means the root itself
    backend_dir: Optional[str] = None
    frontend_test_cmd: Optional[str] = None
    frontend_build_cmd: Optional[str] = None
    frontend_lint_cmd: Optional[str] = None
    backend_test_cmd: Optional[str] = None
    backend_lint_cmd: Optional[str] = None
    playwright_config: bool = False
    has_java: bool = False
    java_dir: Optional[str] = None
    java_build_cmd: Optional[str] = None   # produces the .jar (Maven "package" / Gradle "build")
    java_test_cmd: Optional[str] = None

    def to_dict(self) -> dict:
        return self.__dict__


def _find_candidate_dirs(workspace_path: str, marker_names: List[str], preferred: List[str],
                          max_depth: int = 3) -> List[str]:
    """Directories (relative to workspace_path, '' for the root itself) containing any of
    `marker_names`, root and `preferred` names first — covers both a flat repo (backend/frontend at
    the root, the common case) and a nested monorepo (apps/<name>/backend, apps/<name>/frontend)
    without an unbounded recursive walk. `max_depth` counts path segments from the root (root=0),
    so the default of 3 reaches e.g. apps/<name>/backend."""
    found: List[str] = []
    for name in preferred:
        d = os.path.join(workspace_path, name) if name else workspace_path
        if any(os.path.isfile(os.path.join(d, m)) for m in marker_names):
            found.append(name)
    if found:
        return found
    for dirpath, dirnames, filenames in os.walk(workspace_path):
        rel = os.path.relpath(dirpath, workspace_path)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        if any(m in filenames for m in marker_names):
            found.append("" if rel == "." else rel)
        if depth >= max_depth:
            dirnames[:] = []  # stop descending past max_depth, but this level was still checked
    return found


def discover(workspace_path: str) -> TestingProfile:
    profile = TestingProfile()

    fe_dirs = _find_candidate_dirs(workspace_path, ["package.json"], ["frontend", ""])
    if fe_dirs:
        fe_dir = fe_dirs[0]
        pkg_path = os.path.join(workspace_path, fe_dir, "package.json")
        try:
            with open(pkg_path) as fh:
                pkg = json.load(fh)
            scripts = pkg.get("scripts", {})
            profile.has_frontend = True
            profile.frontend_dir = fe_dir
            if "test" in scripts:
                profile.frontend_test_cmd = "npm test"
            if "build" in scripts:
                profile.frontend_build_cmd = "npm run build"
            if "lint" in scripts:
                profile.frontend_lint_cmd = "npm run lint"
        except (OSError, json.JSONDecodeError):
            pass

    be_dirs = _find_candidate_dirs(
        workspace_path, ["pytest.ini", "requirements.txt", "pyproject.toml"], ["backend", ""])
    if be_dirs:
        be_dir = be_dirs[0]
        backend_dir = os.path.join(workspace_path, be_dir)
        profile.has_backend = True
        profile.backend_dir = be_dir
        profile.backend_test_cmd = "python -m pytest -q"
        if os.path.isfile(os.path.join(backend_dir, "ruff.toml")):
            profile.backend_lint_cmd = "python -m ruff check ."

    # Maven (pom.xml) or Gradle (build.gradle[.kts]) — the two build systems virtually every JVM
    # project (including Bukkit/Spigot/Paper Minecraft plugins) uses. Prefers the project's own
    # wrapper script (./mvnw, ./gradlew) when present — it pins an exact, known-working build-tool
    # version, so it's more reliable than whatever "mvn"/"gradle" happens to be on PATH. On Windows
    # the wrapper is a DIFFERENT file (mvnw.cmd/gradlew.bat, not the Unix mvnw/gradlew shell
    # script a real Maven/Gradle wrapper also ships) — picking the Unix one there would try to run
    # a "#!/bin/sh" script directly, which Windows can't execute at all.
    java_dirs = _find_candidate_dirs(
        workspace_path, ["pom.xml", "build.gradle", "build.gradle.kts"], ["", "plugin"])
    if java_dirs:
        java_dir = java_dirs[0]
        jdir = os.path.join(workspace_path, java_dir)
        profile.has_java = True
        profile.java_dir = java_dir
        if os.path.isfile(os.path.join(jdir, "pom.xml")):
            mvnw_name = "mvnw.cmd" if _IS_WINDOWS else "mvnw"
            mvn = f"./{mvnw_name}" if os.path.isfile(os.path.join(jdir, mvnw_name)) else "mvn"
            profile.java_build_cmd = f"{mvn} -B package"
            profile.java_test_cmd = f"{mvn} -B test"
        else:
            gradlew_name = "gradlew.bat" if _IS_WINDOWS else "gradlew"
            gradle = f"./{gradlew_name}" if os.path.isfile(os.path.join(jdir, gradlew_name)) else "gradle"
            profile.java_build_cmd = f"{gradle} build"
            profile.java_test_cmd = f"{gradle} test"

    for cfg_dir in dict.fromkeys(["", "frontend", *fe_dirs]):  # dict.fromkeys = de-dup, keep order
        for cfg_name in ("playwright.config.ts", "playwright.config.js"):
            if os.path.isfile(os.path.join(workspace_path, cfg_dir, cfg_name)):
                profile.playwright_config = True
                break
        if profile.playwright_config:
            break

    return profile


def classify_subsystems(changed_files: List[str]) -> List[str]:
    hits = set()
    joined = "\n".join(changed_files).lower()
    for subsystem, hints in _ROUTE_HINTS.items():
        if any(h in joined for h in hints):
            hits.add(subsystem)
    return sorted(hits)


def select_commands(profile: TestingProfile, changed_files: List[str]) -> List[Dict[str, Optional[str]]]:
    """Risk-based selection: pick the narrowest set of real commands relevant to what changed.
    Each entry is {"type", "command", "cwd"} — `cwd` is relative to the workspace root ("" = root)
    and must be joined onto the workspace path by the caller; never baked into `command` itself."""
    subsystems = classify_subsystems(changed_files)
    commands: List[Dict[str, Optional[str]]] = []
    touches_frontend = "frontend" in subsystems or not changed_files
    touches_backend = "backend" in subsystems or not changed_files
    touches_java = "java" in subsystems or not changed_files
    large_refactor = len(changed_files) > 25

    if (touches_java or large_refactor) and profile.java_test_cmd:
        commands.append({"type": "java_unit", "command": profile.java_test_cmd, "cwd": profile.java_dir})
    if (touches_java or large_refactor) and profile.java_build_cmd:
        commands.append({"type": "build", "command": profile.java_build_cmd, "cwd": profile.java_dir})
    if (touches_backend or large_refactor) and profile.backend_test_cmd:
        commands.append({"type": "backend_unit", "command": profile.backend_test_cmd, "cwd": profile.backend_dir})
    if (touches_backend or large_refactor) and profile.backend_lint_cmd:
        commands.append({"type": "lint", "command": profile.backend_lint_cmd, "cwd": profile.backend_dir})
    if (touches_frontend or large_refactor) and profile.frontend_test_cmd:
        commands.append({"type": "frontend_unit", "command": profile.frontend_test_cmd, "cwd": profile.frontend_dir})
    if (touches_frontend or large_refactor) and profile.frontend_build_cmd:
        commands.append({"type": "build", "command": profile.frontend_build_cmd, "cwd": profile.frontend_dir})
    if (touches_frontend or large_refactor) and profile.frontend_lint_cmd:
        commands.append({"type": "lint", "command": profile.frontend_lint_cmd, "cwd": profile.frontend_dir})
    return commands
