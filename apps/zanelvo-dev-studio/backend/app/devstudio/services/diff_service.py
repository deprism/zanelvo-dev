"""DiffService — turns `git diff` output into the structured shape the Diff UI and Reviewer
agent need (changed files, added/deleted/modified counts, per-file line stats)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from .git_service import GitService


@dataclass
class FileDiff:
    path: str
    change_type: str   # "added" | "deleted" | "modified" | "renamed"
    additions: int = 0
    deletions: int = 0
    old_path: str = None


@dataclass
class DiffSummary:
    files: List[FileDiff] = field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0
    unified_diff: str = ""

    def to_dict(self) -> dict:
        return {
            "files": [f.__dict__ for f in self.files],
            "total_additions": self.total_additions,
            "total_deletions": self.total_deletions,
            "changed_file_count": len(self.files),
        }


_HUNK_ADD = re.compile(r"^\+(?!\+\+)")
_HUNK_DEL = re.compile(r"^-(?!--)")
_DIFF_HEADER = re.compile(r"^diff --git a/(.*?) b/(.*)$")


def parse_unified_diff(raw: str) -> DiffSummary:
    files: List[FileDiff] = []
    current: FileDiff = None
    total_add = total_del = 0
    for line in raw.splitlines():
        m = _DIFF_HEADER.match(line)
        if m:
            if current:
                files.append(current)
            a, b = m.group(1), m.group(2)
            current = FileDiff(path=b, change_type="modified", old_path=a if a != b else None)
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current.change_type = "added"
        elif line.startswith("deleted file mode"):
            current.change_type = "deleted"
        elif line.startswith("rename from") or line.startswith("rename to"):
            current.change_type = "renamed"
        elif _HUNK_ADD.match(line):
            current.additions += 1
            total_add += 1
        elif _HUNK_DEL.match(line):
            current.deletions += 1
            total_del += 1
    if current:
        files.append(current)
    return DiffSummary(files=files, total_additions=total_add, total_deletions=total_del, unified_diff=raw)


async def get_diff_summary(workspace_local_path: str, base_ref: str) -> DiffSummary:
    git = GitService()
    # Mark untracked files intent-to-add first, so newly CREATED files (a from-scratch site is all
    # new files) appear in the diff instead of being silently invisible to `git diff`.
    await git.add_intent(workspace_local_path)
    raw = await git.diff(workspace_local_path, base_ref=base_ref)
    return parse_unified_diff(raw)
