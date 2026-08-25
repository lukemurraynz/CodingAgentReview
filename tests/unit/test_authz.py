"""T016: repo-scoped authorization logic."""

import pytest

from harness.authz import Principal, RepoAccessDenied, principal_from_claims, require_repo_access


def _scoped_principal(*, oid: str = "u1", scopes: frozenset[str] | None = None,
                      repos: frozenset[str] | None = None) -> Principal:
    return Principal(
        oid=oid,
        scopes=scopes if scopes is not None else frozenset({"repo:org/repo:read"}),
        repos=repos if repos is not None else frozenset(),
    )


class TestScoping:
    def test_read_scope_grants_read(self):
        assert _scoped_principal().can_access("org/repo")

    def test_no_scope_denied(self):
        assert not _scoped_principal(scopes=frozenset()).can_access("org/repo")

    def test_wildcard_scope(self):
        p = _scoped_principal(scopes=frozenset({"repo:all:read"}))
        assert p.can_access("any/repo")

    def test_write_requires_write_scope(self):
        p = _scoped_principal()
        assert not p.can_access("org/repo", require_write=True)
        p_w = _scoped_principal(
            scopes=frozenset({"repo:org/repo:read", "repo:org/repo:write"})
        )
        assert p_w.can_access("org/repo", require_write=True)

    def test_repos_allowlist_restricts(self):
        p = _scoped_principal(repos=frozenset({"org/other"}))
        assert not p.can_access("org/repo")


class TestClaimMapping:
    def test_from_claims_scopes(self):
        p = principal_from_claims({"oid": "u9", "scp": "repo:org/x:read repo:org/y:read"})
        assert p.can_access("org/x")
        assert not p.can_access("org/z")

    def test_from_claims_roles_and_repos(self):
        p = principal_from_claims(
            {"oid": "u8", "roles": ["reviewer"], "repos": ["org/a"]}
        )
        assert "reviewer" in p.scopes
        assert p.repos == frozenset({"org/a"})

    def test_require_raises_on_denial(self):
        with pytest.raises(RepoAccessDenied):
            require_repo_access(_scoped_principal(), "org/nope", write=True)
