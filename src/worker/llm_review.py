"""LLM review wiring helpers for repo-specific rules."""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from graph import (
    ApplicableRule,
    build_lens_briefs,
    discover_specification_artifacts,
    load_rules,
    load_specifications,
    merge_specifications,
    resolve_applicable_rules,
)
from lenses.llm import LensUnavailable

ReviewModelClient = Callable[..., object]


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


def resolve_rule_briefs(
    *,
    changed_paths: Sequence[str],
    applicable_rules: Sequence[ApplicableRule] | None,
) -> dict[str, str]:
    """Resolve per-lens rule briefs from caller input or HARNESS_RULES_ROOT."""

    if applicable_rules is not None:
        return build_lens_briefs(tuple(applicable_rules))

    root = os.environ.get("HARNESS_RULES_ROOT")
    if not root:
        return {}
    artifacts = discover_specification_artifacts(root)
    graph = merge_specifications(load_specifications(artifacts), load_rules(artifacts))
    return build_lens_briefs(resolve_applicable_rules(graph, changed_paths=tuple(changed_paths)))


def coerce_applicable_rules(raw: object) -> tuple[ApplicableRule, ...] | None:
    """Accept event payload rule objects without trusting untyped input."""

    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    rules = [item for item in raw if isinstance(item, ApplicableRule)]
    return tuple(rules)


def compose_rule_aware_client(
    base_client: object | None,
    lens_briefs: dict[str, str],
) -> ReviewModelClient:
    """Build a model callable that appends repo-local rule briefs to prompts."""

    async def _call(*, system_prompt: str, user_prompt: str, deployment: str, lens: str) -> dict[str, object]:
        scoped_system, scoped_user = append_rule_briefs(system_prompt, user_prompt, lens, lens_briefs)
        response = await invoke_model(
            base_client,
            system_prompt=scoped_system,
            user_prompt=scoped_user,
            deployment=deployment,
            lens=lens,
        )
        return {
            "text": response.text,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }

    return _call


async def invoke_model(
    model_client: object | None,
    *,
    system_prompt: str,
    user_prompt: str,
    deployment: str,
    lens: str,
) -> ModelResponse:
    """Invoke either the injected client or the Foundry fallback with custom prompts."""

    if model_client is None:
        from lenses.llm import _build_agent

        agent = _build_agent(lens, system_prompt)
        async with agent:
            response = await agent.run(user_prompt)
        usage = getattr(response, "usage", None)
        return ModelResponse(
            text=getattr(response, "text", "") or "",
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )

    result = _invoke_client(
        model_client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        deployment=deployment,
        lens=lens,
    )
    if inspect.isawaitable(result):
        result = await result
    if isinstance(result, str):
        return ModelResponse(text=result)
    if isinstance(result, dict):
        usage = result.get("usage")
        input_tokens = int(result.get("input_tokens", 0) or 0)
        output_tokens = int(result.get("output_tokens", 0) or 0)
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens", input_tokens) or input_tokens)
            output_tokens = int(usage.get("output_tokens", output_tokens) or output_tokens)
        return ModelResponse(text=str(result.get("text", "")), input_tokens=input_tokens, output_tokens=output_tokens)
    usage = getattr(result, "usage", None)
    return ModelResponse(
        text=_extract_text(result),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


def append_rule_briefs(system_prompt: str, user_prompt: str, lens: str, lens_briefs: dict[str, str]) -> tuple[str, str]:
    general = lens_briefs.get("general", "")
    specific = lens_briefs.get(lens, "")
    combined = "\n".join(part for part in (general, specific) if part).strip()
    if not combined:
        return system_prompt, user_prompt
    addition = f"\n\nRepository-specific review rules:\n{combined}"
    return system_prompt + addition, user_prompt + addition


def _invoke_client(model_client: object, **kwargs: object) -> object:
    if callable(model_client):
        return model_client(**kwargs)
    messages = [
        {"role": "system", "content": kwargs["system_prompt"]},
        {"role": "user", "content": kwargs["user_prompt"]},
    ]
    chat_completions = getattr(model_client, "chat_completions", None)
    create_method = getattr(chat_completions, "create", None)
    direct_create = getattr(model_client, "create", None)
    if callable(create_method):
        return create_method(messages=messages, model=kwargs["deployment"], temperature=0.0)
    if callable(direct_create):
        return direct_create(messages=messages, model=kwargs["deployment"], temperature=0.0)
    raise LensUnavailable("injected model client does not expose a supported chat-completion interface")


def _extract_text(response: object) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
    return ""
