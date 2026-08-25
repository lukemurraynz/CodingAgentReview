"""Unified diff parsing edge cases for lens input reconstruction."""

from lenses.diffparse import parse_unified_diff


def test_multi_file_multi_hunk_preserves_actual_line_numbers() -> None:
    diff = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,3 @@
 line1
+added_first
 line2
@@ -10,2 +11,3 @@
 line10
+added_second
 line11
diff --git a/src/b.py b/src/b.py
--- a/src/b.py
+++ b/src/b.py
@@ -1 +1,2 @@
 line1
+b_added
"""
    files = parse_unified_diff(diff)
    assert [file.path for file in files] == ["src/a.py", "src/b.py"]
    assert files[0].line_map == (1, 2, 3, 11, 12, 13)
    assert files[0].added_lines == frozenset({2, 5})
    assert files[1].added_lines == frozenset({2})


def test_deleted_file_keeps_patch_metadata() -> None:
    diff = """diff --git a/tests/unit/test_legacy.py b/tests/unit/test_legacy.py
--- a/tests/unit/test_legacy.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def test_old():
-    assert True
"""
    files = parse_unified_diff(diff)
    assert len(files) == 1
    assert files[0].deleted is True
    assert files[0].path == "tests/unit/test_legacy.py"
    assert files[0].patch_lines[-1] == "-    assert True"


def test_binary_diff_is_skipped() -> None:
    diff = """diff --git a/logo.png b/logo.png
Binary files a/logo.png and b/logo.png differ
"""
    assert parse_unified_diff(diff) == []


def test_new_file_from_dev_null_uses_new_path() -> None:
    diff = """diff --git a/dev/null b/tests/unit/test_new.py
--- /dev/null
+++ b/tests/unit/test_new.py
@@ -0,0 +1,2 @@
+def test_new():
+    assert True
"""
    files = parse_unified_diff(diff)
    assert files[0].path == "tests/unit/test_new.py"
    assert files[0].line_map == (1, 2)


def test_malformed_hunk_header_does_not_raise() -> None:
    diff = """diff --git a/src/bad.py b/src/bad.py
--- a/src/bad.py
+++ b/src/bad.py
@@ malformed @@
+value = 1
"""
    assert parse_unified_diff(diff) == []
