"""LLM wiring: repo rules, exploitability rendering, and worst-of-N second opinions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from graph import ApplicableRule
from harness.models import RiskLevel, Severity
from harness.models.specification import ReviewRule
from lenses.llm import CorrectnessLens, SecurityLens
from providers.formatting import build_comment_body
from worker import runner
from worker.depth import ReviewDepthPolicy


def _event_payload(**extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "changeId": "change-1",
        "headSha": "abc123",
        "repoId": "org/repo",
        "provider": "github",
        "baseSha": "base123",
    }
    payload.update(extra)
    return payload


class FakeAdapter:
    def __init__(self, diff: str) -> None:
        self.diff = diff
        self.posted: list[tuple[object, list[object], object]] = []
        self.summaries: list[tuple[object, list[object], object]] = []

    async def fetch_diff(self, change):
        return self.diff

    async def post_annotations(self, change, findings, report=None):
        self.posted.append((change, findings, report))

    async def upsert_summary_comment(self, change, findings, report=None):
        self.summaries.append((change, findings, report))


class FakeRepository:
    def __init__(self) -> None:
        self.runs = []
        self.findings = []
        self.run_metadata = []

    async def put_run(self, run) -> None:
        self.runs.append(run.model_copy(deep=True))

    async def put_finding(self, finding) -> None:
        self.findings.append(finding.model_copy(deep=True))

    async def query_runs_for_change(self, change_id: str) -> list[dict[str, object]]:
        del change_id
        return []

    async def put_run_metadata(self, run_id: str, metadata: dict[str, object]) -> None:
        self.run_metadata.append((run_id, metadata))


class ScriptedModel:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _policy(*lenses: str) -> ReviewDepthPolicy:
    return ReviewDepthPolicy(
        risk_floor=RiskLevel.HIGH if "security" in lenses else RiskLevel.MEDIUM,
        risk_level=RiskLevel.HIGH if "security" in lenses else RiskLevel.MEDIUM,
        lenses=lenses,
    )


def _response(
    items: Sequence[Mapping[str, object]],
    *,
    input_tokens: int = 5,
    output_tokens: int = 5,
) -> dict[str, object]:
    return {"text": json.dumps(items), "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}


def _brief(rule_id: str, lens: str, instruction: str) -> ApplicableRule:
    return ApplicableRule(
        source_path=".harness/rules/test.md",
        rule=ReviewRule(
            id=rule_id,
            severity=Severity.HIGH,
            applies_to=["src/**"],
            lens=lens,
            instruction=instruction,
        ),
        matched_paths=("src/app.py",),
        match_reason="matched paths: src/app.py",
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "HARNESS_DRY_RUN",
        "HARNESS_BUDGET_INPUT_TOKENS",
        "HARNESS_BUDGET_OUTPUT_TOKENS",
        "HARNESS_BUDGET_COMPUTE_MS",
        "HARNESS_SECOND_OPINION_LIMIT",
        "HARNESS_RULES_ROOT",
        "HARNESS_COSMOS_ENDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def llm_state(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeRepository, FakeAdapter]:
    repository = FakeRepository()
    diff = "diff --git a/src/app.py b/src/app.py\n+++ b/src/app.py\n@@ -0,0 +1,2 @@\n+@app.get('/admin')\n+return data"
    adapter = FakeAdapter(diff)
    monkeypatch.setitem(runner.ADAPTERS, "github", adapter)
    monkeypatch.setattr(runner, "_make_repository", lambda: repository)
    monkeypatch.setattr(runner, "LENS_REGISTRY", {})
    monkeypatch.setattr(runner, "LLM_LENSES", {"correctness": CorrectnessLens(), "security": SecurityLens()})
    return repository, adapter


async def test_rule_briefs_are_appended_to_llm_prompts(llm_state, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _adapter = llm_state
    model = ScriptedModel([_response([])])
    monkeypatch.setattr(runner, "classify_review_depth", lambda **_: _policy("correctness"))

    await runner.execute_review(
        _event_payload(
            modelClient=model,
            applicableRules=[
                _brief("general-rule", "general", "Always consider repo-level guardrails."),
                _brief("correctness-rule", "correctness", "Watch for changed error handling."),
            ],
        )
    )

    assert len(model.calls) == 1
    assert "general-rule" in str(model.calls[0]["system_prompt"])
    assert "correctness-rule" in str(model.calls[0]["user_prompt"])


async def test_harness_rules_root_resolves_rule_briefs(
    llm_state, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _adapter = llm_state
    rules_dir = tmp_path / ".harness" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "security.md").write_text(
        "---\n"
        "id: route-guard\n"
        "severity: blocker\n"
        "applies_to: ['src/**']\n"
        "lens: general\n"
        "---\n"
        "Require auth on admin routes.\n",
        encoding="utf-8",
    )
    model = ScriptedModel([_response([])])
    monkeypatch.setenv("HARNESS_RULES_ROOT", str(tmp_path))
    monkeypatch.setattr(runner, "classify_review_depth", lambda **_: _policy("correctness"))

    await runner.execute_review(_event_payload(modelClient=model))

    assert "route-guard" in str(model.calls[0]["user_prompt"])


async def test_security_findings_render_exploitability_in_summary(llm_state, monkeypatch: pytest.MonkeyPatch) -> None:
    _, adapter = llm_state
    items = [
        {
            "severity": "medium",
            "title": "Missing authorization guard",
            "detail": "Route lacks an access check.",
            "path": "src/app.py",
            "line": 1,
        }
    ]
    model = ScriptedModel([_response(items), _response([])])
    monkeypatch.setattr(runner, "LLM_LENSES", {"security": SecurityLens()})
    monkeypatch.setattr(runner, "classify_review_depth", lambda **_: _policy("security"))

    await runner.execute_review(_event_payload(modelClient=model))
    body = build_comment_body(adapter.summaries[0][1], adapter.summaries[0][2])

    assert "Exploitability: REACHABLE" in body
    assert adapter.summaries[0][2].gate.status == "block"


async def test_second_opinion_agree_leaves_severity_unchanged(llm_state, monkeypatch: pytest.MonkeyPatch) -> None:
    repository, _adapter = llm_state
    high = [{"severity": "high", "title": "Logic break", "detail": "detail", "path": "src/app.py", "line": 2}]
    model = ScriptedModel([_response(high), _response(high)])
    monkeypatch.setattr(runner, "classify_review_depth", lambda **_: _policy("correctness"))

    await runner.execute_review(_event_payload(modelClient=model))

    assert repository.findings[0].severity == Severity.HIGH
    assert "Adversarial second opinion" in str(model.calls[1]["user_prompt"])


async def test_second_opinion_escalates_to_more_severe_verdict(llm_state, monkeypatch: pytest.MonkeyPatch) -> None:
    repository, _adapter = llm_state
    first = [{"severity": "high", "title": "Logic break", "detail": "detail", "path": "src/app.py", "line": 2}]
    second = [{"severity": "blocker", "title": "Logic break", "detail": "detail", "path": "src/app.py", "line": 2}]
    model = ScriptedModel([_response(first), _response(second)])
    monkeypatch.setattr(runner, "classify_review_depth", lambda **_: _policy("correctness"))

    await runner.execute_review(_event_payload(modelClient=model))

    assert repository.findings[0].severity == Severity.BLOCKER


async def test_second_opinion_budget_exhaustion_skips_and_degrades(llm_state, monkeypatch: pytest.MonkeyPatch) -> None:
    repository, _adapter = llm_state
    high = [{"severity": "high", "title": "Logic break", "detail": "detail", "path": "src/app.py", "line": 2}]
    model = ScriptedModel([_response(high, input_tokens=10, output_tokens=0)])
    monkeypatch.setenv("HARNESS_BUDGET_INPUT_TOKENS", "1")
    monkeypatch.setattr(runner, "classify_review_depth", lambda **_: _policy("correctness"))

    run = await runner.execute_review(_event_payload(modelClient=model))

    assert run.status == runner.RunStatus.DEGRADED
    assert any(reason.startswith("second_opinion_skipped_budget:") for reason in run.degraded_reasons)
    assert repository.findings[0].severity == Severity.HIGH


async def test_second_opinion_cap_hit_marks_degraded(llm_state, monkeypatch: pytest.MonkeyPatch) -> None:
    repository, _adapter = llm_state
    first = [
        {"severity": "high", "title": "One", "detail": "detail", "path": "src/app.py", "line": 1},
        {"severity": "high", "title": "Two", "detail": "detail", "path": "src/app.py", "line": 2},
    ]
    second = [{"severity": "blocker", "title": "One", "detail": "detail", "path": "src/app.py", "line": 1}]
    model = ScriptedModel([_response(first), _response(second)])
    monkeypatch.setenv("HARNESS_SECOND_OPINION_LIMIT", "1")
    monkeypatch.setattr(runner, "classify_review_depth", lambda **_: _policy("correctness"))

    run = await runner.execute_review(_event_payload(modelClient=model))

    assert run.status == runner.RunStatus.DEGRADED
    assert any(reason.startswith("second_opinion_cap:") for reason in run.degraded_reasons)
    severities = {finding.evidence[0].line_start: finding.severity for finding in repository.findings}
    assert severities[1] == Severity.BLOCKER
    assert severities[2] == Severity.HIGH
