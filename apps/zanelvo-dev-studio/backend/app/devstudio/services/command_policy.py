"""CommandPolicy — deterministic allow/deny for anything Dev Studio's ExecutionService runs.

Pure, dependency-free logic so it is trivially unit-testable (see tests/devstudio/test_command_policy.py).
Deny-list is checked first and always wins; a command must additionally match one of the allowed
prefixes to run at all (default-deny, not default-allow).
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import List

# Prefixes considered safe to run inside an isolated task workspace. Matched against the
# shlex-split command list, so "npm test -- --watch=false" matches ["npm", "test"].
ALLOWED_PREFIXES: List[List[str]] = [
    ["npm", "install"], ["npm", "ci"], ["npm", "test"], ["npm", "run"],
    ["yarn"], ["yarn", "install"], ["yarn", "test"], ["yarn", "run"], ["yarn", "build"],
    ["pnpm", "install"], ["pnpm", "test"], ["pnpm", "run"],
    ["pytest"], ["python", "-m", "pytest"], ["python3", "-m", "pytest"],
    ["python", "-m", "ruff"], ["python3", "-m", "ruff"], ["ruff"],
    ["python", "-m", "flake8"], ["flake8"],
    ["python", "-m", "mypy"], ["mypy"],
    ["pip", "install"], ["pip3", "install"],
    ["npx", "playwright"], ["npx", "tsc"],
    ["git", "status"], ["git", "diff"], ["git", "log"],
    # --- JVM builds (Java/Kotlin projects — e.g. Bukkit/Spigot/Paper Minecraft plugins) ---
    # .cmd/.bat entries are the Windows wrapper scripts (see testing_service.py's platform check —
    # ./mvnw/./gradlew are Unix shell scripts that don't run on Windows at all).
    ["mvn"], ["./mvnw"], ["./mvnw.cmd"], ["gradle"], ["./gradlew"], ["./gradlew.bat"],
    ["javac"], ["java"], ["jar"],
    # --- archiving (build artifacts, general packaging) ---
    ["zip"], ["unzip"], ["tar"],
]

# Substring / regex patterns that are ALWAYS refused, no matter what else matches. Matched against
# the raw command string (case-insensitive) so we catch shell metacharacter smuggling attempts too.
DENY_PATTERNS = [
    r"\brm\s+-rf\s+/(?!\S)",           # rm -rf /
    r"\brm\s+-rf\s+/\*",
    r"\bmkfs\b", r"\bdd\s+if=", r":\(\)\s*\{\s*:\|:", r"\bshutdown\b", r"\breboot\b",
    r"\bforce[- ]?push\b", r"\bgit\s+push\s+.*--force", r"\bgit\s+push\s+.*-f\b",
    r"\bgit\s+reset\s+--hard\s+origin", r"\bgit\s+clean\s+-[a-z]*f",
    r"\bdrop\s+(table|database)\b", r"\btruncate\s+table\b",
    r"\bsudo\b", r"\bsu\s+-", r"\bchmod\s+777\b", r"\bchown\s+-R\s+/",
    r">\s*/dev/sd", r"\bmv\s+.*\s+/dev/null",
    r"\bcurl\b.*\|\s*sh\b", r"\bwget\b.*\|\s*sh\b",
    r"\bnc\s+-l\b", r"\b/etc/passwd\b", r"\b/etc/shadow\b",
    r"&&\s*rm\s+-rf", r";\s*rm\s+-rf",
]

_DENY_RE = [re.compile(p, re.IGNORECASE) for p in DENY_PATTERNS]


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str


def evaluate(command: str) -> PolicyDecision:
    if not command or not command.strip():
        return PolicyDecision(False, "Empty command")
    for pattern in _DENY_RE:
        if pattern.search(command):
            return PolicyDecision(False, f"Command matches a blocked destructive pattern: {pattern.pattern}")
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        return PolicyDecision(False, f"Could not parse command safely: {e}")
    if not tokens:
        return PolicyDecision(False, "Empty command")
    for prefix in ALLOWED_PREFIXES:
        if tokens[: len(prefix)] == prefix:
            return PolicyDecision(True, "Matches allowed prefix")
    return PolicyDecision(False, f"Command not on the allowlist: {tokens[0]!r}. "
                                   "Add it to ALLOWED_PREFIXES if it is genuinely safe.")


def is_allowed(command: str) -> bool:
    return evaluate(command).allowed
