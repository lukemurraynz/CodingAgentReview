"""Repo-grant extraction from token claims (repos custom claim + Entra app roles)."""

from harness.authn import repo_ids_from_claims


def test_repos_claim_grants():
    assert repo_ids_from_claims({"repos": ["org/a", "org/b"]}) == frozenset({"org/a", "org/b"})


def test_roles_claim_values_are_repo_ids():
    assert repo_ids_from_claims({"roles": ["org/repo", "org/other"]}) == frozenset({"org/repo", "org/other"})


def test_union_of_both_claims():
    claims: dict[str, object] = {"repos": ["org/a"], "roles": ["org/b"]}
    assert repo_ids_from_claims(claims) == frozenset({"org/a", "org/b"})


def test_missing_or_malformed_claims_grant_nothing():
    assert repo_ids_from_claims({}) == frozenset()
    assert repo_ids_from_claims({"repos": "org/a"}) == frozenset()
    assert repo_ids_from_claims({"roles": {"org": ["a"]}}) == frozenset()
