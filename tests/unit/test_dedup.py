"""T010: dedup key determinism + normalization semantics."""

from harness.dedup import make_key


def _key(path: str, content: str) -> str:
    return make_key(path, content)


class TestDeterminism:
    def test_same_inputs_same_key(self):
        assert _key("src/app.py", "x = 1\n") == _key("src/app.py", "x = 1\n")

    def test_versioned_prefix(self):
        assert _key("a.py", "x").startswith("v1:")


class TestNormalization:
    def test_path_separators_and_case(self):
        assert _key("SRC\\App.PY", "x") == _key("src/app.py", "x")

    def test_trailing_whitespace_ignored(self):
        assert _key("a.py", "x = 1   \n") == _key("a.py", "x = 1\n")

    def test_crlf_lf_equal(self):
        assert _key("a.py", "l1\r\nl2\r\n") == _key("a.py", "l1\nl2\n")

    def test_content_change_changes_key(self):
        assert _key("a.py", "x = 1") != _key("a.py", "x = 2")

    def test_different_file_different_key(self):
        assert _key("a.py", "x") != _key("b.py", "x")
