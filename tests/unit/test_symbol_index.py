"""Repo symbol index tests."""

from __future__ import annotations

from pathlib import Path

from graph import build_symbol_index


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_symbol_index_collects_defs_calls_registrations_and_routes(tmp_path: Path) -> None:
    _write(
        tmp_path / "app.py",
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "class MetricsClient:\n"
        "    pass\n\n"
        "@app.get('/health')\n"
        "def health() -> MetricsClient:\n"
        "    return MetricsClient()\n",
    )
    _write(
        tmp_path / "services.py",
        "from di import container\n"
        "from app import MetricsClient\n\n"
        "def configure() -> None:\n"
        "    container.add_singleton(MetricsClient, MetricsClient)\n",
    )
    _write(
        tmp_path / "flask_routes.py",
        "from flask import Flask\n\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/submit', methods=['POST'])\n"
        "def submit() -> str:\n"
        "    return 'ok'\n",
    )

    index = build_symbol_index(tmp_path)

    assert index.definitions["MetricsClient"][0] == ("app.py", 5, "ClassDef")
    assert index.registrations["MetricsClient"][0] == ("services.py", 5)
    assert index.invocation_count("MetricsClient") == 1
    assert index.invocations["MetricsClient"].per_path[0] == ("app.py", 1)
    assert index.is_referenced_elsewhere("MetricsClient") is True
    assert ("app.py", "GET", "/health") in set(index.routes)
    assert ("flask_routes.py", "POST", "/submit") in set(index.routes)


def test_symbol_index_tolerates_parse_errors_and_excludes_tests_by_default(tmp_path: Path) -> None:
    _write(tmp_path / "pkg" / "live.py", "class LiveService:\n    pass\n")
    _write(tmp_path / "pkg" / "broken.py", "def nope(:\n")
    _write(tmp_path / "tests" / "test_live.py", "LiveService()\n")
    _write(tmp_path / ".venv" / "shadow.py", "LiveService()\n")
    _write(tmp_path / "node_modules" / "shadow.py", "LiveService()\n")

    excluded_index = build_symbol_index(tmp_path)
    included_index = build_symbol_index(tmp_path, include_tests=True)

    assert excluded_index.invocation_count("LiveService") == 0
    assert included_index.invocation_count("LiveService") == 1
    assert excluded_index.parse_issues[0].path == "pkg/broken.py"


def test_symbol_index_is_deterministic(tmp_path: Path) -> None:
    _write(tmp_path / "b.py", "class Beta:\n    pass\n")
    _write(tmp_path / "a.py", "from b import Beta\n\nBeta()\n")

    first = build_symbol_index(tmp_path)
    second = build_symbol_index(tmp_path)

    assert first == second
