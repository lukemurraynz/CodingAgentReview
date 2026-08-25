"""Agentic Engineering Harness — shared domain library.

Pure domain logic only: no Azure SDK imports at module import time.
Azure-backed adapters live behind lazy imports and are covered by
env-gated integration tests.
"""

__version__ = "0.1.0"
