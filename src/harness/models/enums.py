"""Domain enumerations shared across the harness."""

from enum import StrEnum


class ChangeType(StrEnum):
    PULL_REQUEST = "pull_request"
    COMMIT = "commit"
    ADHOC_DIFF = "adhoc_diff"


class ChangeClassification(StrEnum):
    CODE = "code"
    DOCS = "docs"
    GENERATED = "generated"
    LOCKFILE = "lockfile"
    MIXED = "mixed"


class GitProvider(StrEnum):
    GITHUB = "github"
    AZURE_DEVOPS = "azure_devops"


class Severity(StrEnum):
    BLOCKER = "blocker"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    TEST_QUALITY = "test_quality"
    STRUCTURAL = "structural"
    PRODUCTION_VALIDATION = "production_validation"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
