"""Repo-scoped authorization logic (FR-019).

Pure claim-checking; token signature validation happens at the host boundary
(controlplane / mcpserver) against the configured Entra authority.
"""

from dataclasses import dataclass


class RepoAccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    oid: str  # Entra object id
    scopes: frozenset[str]
    # repos: explicitly granted repo ids ("org/repo"); empty set = all granted via role
    repos: frozenset[str]

    def can_access(self, repo_id: str, *, require_write: bool = False) -> bool:
        if f"repo:{repo_id}:read" not in self.scopes and "repo:all:read" not in self.scopes:
            return False
        if require_write:
            if f"repo:{repo_id}:write" not in self.scopes and "repo:all:write" not in self.scopes:
                return False
        if self.repos and repo_id not in self.repos:
            return False
        return True


def principal_from_claims(claims: dict[str, object]) -> Principal:
    """Map validated JWT claims to a Principal.

    Expected claims: ``oid`` (user/service principal id), optional ``roles``
    or ``scp`` space-delimited scopes, optional ``repos`` extension claim.
    """
    raw_scopes = str(claims.get("scp") or "")
    roles = claims.get("roles")
    scope_list = raw_scopes.split()
    if isinstance(roles, list):
        scope_list.extend(str(r) for r in roles)
    repos_claim = claims.get("repos")
    repos = {str(r) for r in repos_claim} if isinstance(repos_claim, list) else set()
    return Principal(
        oid=str(claims.get("oid") or ""),
        scopes=frozenset(scope_list),
        repos=frozenset(repos),
    )


def require_repo_access(principal: Principal, repo_id: str, *, write: bool = False) -> None:
    if not principal.can_access(repo_id, require_write=write):
        raise RepoAccessDenied(f"principal {principal.oid} lacks access to {repo_id}")
