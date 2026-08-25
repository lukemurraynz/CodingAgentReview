"""T008: secret redaction before persistence (FR-020)."""

from harness.redaction import redact_text


class TestPatterns:
    def test_aws_access_key(self):
        out, n = redact_text("key AKIAIOSFODNN7EXAMPLE in config")
        assert "AKIAIOSFODNN7EXAMPLE" not in out
        assert "[REDACTED:aws_access_key]" in out
        assert n == 1

    def test_github_token(self):
        token = "ghp_" + "a" * 36
        out, _ = redact_text(f"token {token}")
        assert token not in out

    def test_account_key(self):
        key = "AccountKey=" + "x" * 60
        out, _ = redact_text(f"conn: {key};EndpointSuffix=core.windows.net")
        assert key not in out
        assert "[REDACTED:account_key]" in out

    def test_private_key_block_multiline(self):
        blob = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nmore\n-----END RSA PRIVATE KEY-----"
        out, _ = redact_text(f"cert:\n{blob}\nend")
        assert "MIIEow" not in out

    def test_generic_secret_kv(self):
        out, n = redact_text("client_secret=supersecretvalue99, scope=x")
        assert "supersecretvalue99" not in out
        assert n >= 1

    def test_clean_text_untouched(self):
        clean = "def add(a, b):\n    return a + b\n"
        out, n = redact_text(clean)
        assert out == clean
        assert n == 0

    def test_counts_all_replacements(self):
        text = "AKIAIOSFODNN7EXAMPLE and ghp_" + "b" * 36
        _, n = redact_text(text)
        assert n == 2
