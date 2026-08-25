"""LLM lens degraded-path tests using injected fake model clients."""

import pytest

from lenses import LensContext, LensFile
from lenses.llm import CorrectnessLens, LensUnavailable


def _ctx(*, model_client: object | None, model_deployment: str = "") -> LensContext:
    return LensContext(
        change_id="c1",
        repo_id="org/repo",
        files=[
            LensFile(
                path="src/app.py",
                content="def run() -> None:\n    pass\n",
                added_lines=frozenset({1, 2}),
                line_map=(10, 11),
            )
        ],
        model_client=model_client,
        model_deployment=model_deployment,
    )


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, text: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.text = text
        self.usage = _Usage(input_tokens, output_tokens)


class _FakeChatCompletions:
    def __init__(self, response: object) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response: object) -> None:
        self.chat_completions = _FakeChatCompletions(response)


@pytest.mark.asyncio
class TestLLMLens:
    async def test_missing_endpoint_without_injection_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
        with pytest.raises(LensUnavailable):
            await CorrectnessLens().run(_ctx(model_client=None))

    async def test_malformed_json_response_returns_no_findings(self) -> None:
        lens = CorrectnessLens()
        findings = await lens.run(_ctx(model_client=_FakeClient(_Response("not json", 7, 3))))
        assert findings == []
        assert lens.last_usage == {"input_tokens": 7, "output_tokens": 3}

    async def test_out_of_range_values_are_clamped_and_severity_defaults(self) -> None:
        payload = '[{"severity":"critical","title":"bad","detail":"x","path":"src/app.py","line":999}]'
        findings = await CorrectnessLens().run(_ctx(model_client=_FakeClient(_Response(payload))))
        assert findings[0].severity.value == "medium"
        assert findings[0].evidence[0].line_start == 11

    async def test_more_than_twenty_findings_are_truncated(self) -> None:
        items = [
            {"severity": "low", "title": f"f{index}", "detail": "d", "path": "src/app.py", "line": 10}
            for index in range(25)
        ]
        findings = await CorrectnessLens().run(_ctx(model_client=lambda **_: {"text": __import__("json").dumps(items)}))
        assert len(findings) == 20

    async def test_usage_accounting_from_dict_response(self) -> None:
        response = {
            "text": '[{"severity":"low","title":"ok","detail":"d","path":"src/app.py","line":10}]',
            "usage": {"input_tokens": 12, "output_tokens": 4},
        }
        lens = CorrectnessLens()
        findings = await lens.run(_ctx(model_client=lambda **_: response, model_deployment="custom-model"))
        assert len(findings) == 1
        assert lens.last_usage == {"input_tokens": 12, "output_tokens": 4}
