"""FileService — safe file tools scoped to one root directory (a workspace or browse checkout).

Every path is resolved and checked against the root before any filesystem operation, so no caller
— agent-generated path included — can escape the workspace (path traversal protection). Patches
carry a `expected_hash` so one agent can never silently clobber another's concurrent edit to the
same file (stale patch protection): the hash is re-checked immediately before writing.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
from dataclasses import dataclass
from typing import List, Optional

_EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".next", "dist", "build", ".venv",
                  "venv", ".pytest_cache", ".mypy_cache", "coverage", ".turbo"}
_MAX_READ_BYTES = 400_000
_BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2",
               ".ttf", ".eot", ".zip", ".gz", ".mp4", ".mp3", ".sqlite", ".db"}


class PathEscapeError(Exception):
    pass


class StalePatchError(Exception):
    """Raised when a patch's expected_hash no longer matches the file on disk — another agent
    (or the founder) changed it since this agent last read it. Caller must reload and retry."""


@dataclass
class FileEntry:
    path: str
    is_dir: bool
    size: Optional[int] = None


def _resolve(root: str, rel_path: str) -> str:
    root = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root, rel_path.lstrip("/")))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise PathEscapeError(f"Path escapes workspace root: {rel_path!r}")
    return candidate


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]


class FileService:
    def __init__(self, root: str):
        self.root = os.path.realpath(root)

    def list_tree(self, subdir: str = "", max_entries: int = 4000) -> List[FileEntry]:
        base = _resolve(self.root, subdir)
        out: List[FileEntry] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS and not d.startswith(".")
                           or d in (".github",)]
            rel_dir = os.path.relpath(dirpath, self.root)
            if rel_dir != ".":
                out.append(FileEntry(path=rel_dir, is_dir=True))
            for f in filenames:
                if len(out) >= max_entries:
                    return out
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, self.root)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = None
                out.append(FileEntry(path=rel, is_dir=False, size=size))
        return out

    def read_file(self, rel_path: str) -> str:
        full = _resolve(self.root, rel_path)
        if not os.path.isfile(full):
            raise FileNotFoundError(rel_path)
        ext = os.path.splitext(full)[1].lower()
        if ext in _BINARY_EXT:
            raise ValueError(f"Refusing to read binary file as text: {rel_path}")
        with open(full, "rb") as fh:
            data = fh.read(_MAX_READ_BYTES + 1)
        truncated = len(data) > _MAX_READ_BYTES
        text = data[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
        return text + ("\n... [truncated]" if truncated else "")

    def read_files(self, rel_paths: List[str]) -> dict:
        out = {}
        for p in rel_paths:
            try:
                out[p] = self.read_file(p)
            except Exception as e:  # noqa: BLE001
                out[p] = f"<<error reading file: {e}>>"
        return out

    def read_hash(self, rel_path: str) -> Optional[str]:
        full = _resolve(self.root, rel_path)
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as fh:
            return file_hash(fh.read())

    def read_bytes(self, rel_path: str, max_bytes: int = 25_000_000) -> bytes:
        """Raw bytes of a workspace file (path-safe), for serving a live static preview — unlike
        read_file this doesn't refuse binaries (images/fonts a site needs) or decode as text."""
        full = _resolve(self.root, rel_path)
        if not os.path.isfile(full):
            raise FileNotFoundError(rel_path)
        with open(full, "rb") as fh:
            return fh.read(max_bytes)

    def search_repo(self, query: str, glob: Optional[str] = None, max_results: int = 200) -> List[dict]:
        results = []
        query_lower = query.lower()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS and not d.startswith(".")]
            for f in filenames:
                if glob and not fnmatch.fnmatch(f, glob):
                    continue
                if os.path.splitext(f)[1].lower() in _BINARY_EXT:
                    continue
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, self.root)
                try:
                    if os.path.getsize(full) > _MAX_READ_BYTES:
                        continue
                    with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh, 1):
                            if query_lower in line.lower():
                                results.append({"path": rel, "line": i, "text": line.strip()[:300]})
                                if len(results) >= max_results:
                                    return results
                except OSError:
                    continue
        return results

    def create_file(self, rel_path: str, content: str) -> str:
        full = _resolve(self.root, rel_path)
        if os.path.exists(full):
            raise FileExistsError(rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        return self.read_hash(rel_path) or ""

    def replace_file(self, rel_path: str, content: str, expected_hash: Optional[str] = None) -> str:
        current = self.read_hash(rel_path)
        if expected_hash is not None and current is not None and current != expected_hash:
            raise StalePatchError(f"{rel_path} changed on disk (expected {expected_hash}, found {current})")
        full = _resolve(self.root, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        return self.read_hash(rel_path) or ""

    def patch_file(self, rel_path: str, find: str, replace: str, expected_hash: Optional[str] = None,
                    count: int = 1) -> str:
        """Simple, safe find/replace patch (agents send exact-match snippets, like the Edit tool).
        Refuses to apply if `find` is not found, or found more than `count` times unless count<=0."""
        current_hash = self.read_hash(rel_path)
        if expected_hash is not None and current_hash is not None and current_hash != expected_hash:
            raise StalePatchError(f"{rel_path} changed on disk (expected {expected_hash}, found {current_hash})")
        text = self.read_file(rel_path)
        occurrences = text.count(find)
        if occurrences == 0:
            raise ValueError(f"Patch target not found in {rel_path}")
        if count > 0 and occurrences != count:
            raise ValueError(
                f"Patch target found {occurrences} times in {rel_path}, expected {count}; "
                "make the search string more specific."
            )
        new_text = text.replace(find, replace, count if count > 0 else -1)
        return self.replace_file(rel_path, new_text, expected_hash=current_hash)

    def delete_file(self, rel_path: str) -> None:
        full = _resolve(self.root, rel_path)
        if os.path.isdir(full):
            shutil.rmtree(full)
        elif os.path.isfile(full):
            os.remove(full)
        else:
            raise FileNotFoundError(rel_path)

    def move_file(self, src_rel: str, dst_rel: str) -> None:
        src = _resolve(self.root, src_rel)
        dst = _resolve(self.root, dst_rel)
        if not os.path.exists(src):
            raise FileNotFoundError(src_rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
