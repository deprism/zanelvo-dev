"""Deterministic tests for DiffService's unified-diff parser. Pure string parsing, no git/DB."""
from app.devstudio.services.diff_service import parse_unified_diff

_SAMPLE_DIFF = """\
diff --git a/backend/app/foo.py b/backend/app/foo.py
index e69de29..8c7e5a1 100644
--- a/backend/app/foo.py
+++ b/backend/app/foo.py
@@ -1,3 +1,4 @@
 def foo():
-    return 1
+    return 2
+    # extra line

diff --git a/backend/app/new_file.py b/backend/app/new_file.py
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/backend/app/new_file.py
@@ -0,0 +1,2 @@
+def bar():
+    pass
diff --git a/backend/app/old_file.py b/backend/app/old_file.py
deleted file mode 100644
index e69de29..0000000
--- a/backend/app/old_file.py
+++ /dev/null
@@ -1,1 +0,0 @@
-def gone():
"""


def test_parses_all_three_files():
    summary = parse_unified_diff(_SAMPLE_DIFF)
    paths = {f.path: f for f in summary.files}
    assert set(paths) == {"backend/app/foo.py", "backend/app/new_file.py", "backend/app/old_file.py"}


def test_change_types():
    summary = parse_unified_diff(_SAMPLE_DIFF)
    by_path = {f.path: f for f in summary.files}
    assert by_path["backend/app/foo.py"].change_type == "modified"
    assert by_path["backend/app/new_file.py"].change_type == "added"
    assert by_path["backend/app/old_file.py"].change_type == "deleted"


def test_line_counts():
    summary = parse_unified_diff(_SAMPLE_DIFF)
    by_path = {f.path: f for f in summary.files}
    assert by_path["backend/app/foo.py"].additions == 2
    assert by_path["backend/app/foo.py"].deletions == 1
    assert by_path["backend/app/new_file.py"].additions == 2
    assert by_path["backend/app/old_file.py"].deletions == 1
    assert summary.total_additions == 4
    assert summary.total_deletions == 2


def test_empty_diff_is_no_changes():
    summary = parse_unified_diff("")
    assert summary.files == []
    assert summary.total_additions == 0
    assert summary.total_deletions == 0


def test_to_dict_shape():
    summary = parse_unified_diff(_SAMPLE_DIFF)
    d = summary.to_dict()
    assert d["changed_file_count"] == 3
    assert d["total_additions"] == 4
    assert d["total_deletions"] == 2
    assert isinstance(d["files"], list)


# --- untracked (newly-created) files must appear in the diff -----------------------------------

def test_get_diff_summary_includes_untracked_new_files(tmp_path):
    """A from-scratch website is all NEW files; plain `git diff` ignores untracked files, so the
    summary used to come back empty (0 changes) even though the agents wrote real code. Regression:
    get_diff_summary now marks intent-to-add first, so new files are counted as additions."""
    import asyncio
    import subprocess

    from app.devstudio.services.diff_service import get_diff_summary

    repo = str(tmp_path)
    run = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True,
                                     capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@t.t")
    run("config", "user.name", "t")
    (tmp_path / "README.md").write_text("base\n")
    run("add", "-A")
    run("commit", "-qm", "base")
    base = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"]).decode().strip()

    # Create brand-new, untracked files (the whole-new-site case).
    (tmp_path / "index.html").write_text("<h1>hi</h1>\n")
    (tmp_path / "game.js").write_text("console.log('play')\n")

    summary = asyncio.run(get_diff_summary(repo, base)).to_dict()
    assert summary["changed_file_count"] == 2
    assert {f["path"] for f in summary["files"]} == {"index.html", "game.js"}
    assert summary["total_additions"] >= 2
