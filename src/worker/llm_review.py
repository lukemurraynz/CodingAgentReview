"""LLM review wiring helpers for repo-specific rules."""

from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from graph import (
    ApplicableRule,
    SymbolIndex,
    build_lens_briefs,
    discover_specification_artifacts,
    load_rules,
    load_specifications,
    merge_specifications,
    resolve_applicable_rules,
)
from lenses.llm import LensUnavailable

ReviewModelClient = Callable[..., Awaitable[dict[str, object]]]


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
    trusted_user_context: str = "",
) -> ReviewModelClient:
    """Build a model callable that appends repo-local rule briefs to prompts."""

    async def _call(*, system_prompt: str, user_prompt: str, deployment: str, lens: str) -> dict[str, object]:
        scoped_user = prepend_trusted_context(user_prompt, trusted_user_context)
        scoped_system, scoped_user = append_rule_briefs(system_prompt, scoped_user, lens, lens_briefs)
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


def prepend_trusted_context(user_prompt: str, trusted_user_context: str) -> str:
    """Prepend trusted repository context ahead of the untrusted diff prompt."""

    if not trusted_user_context:
        return user_prompt
    return f"{trusted_user_context}\n\n{user_prompt}"


def build_trusted_symbol_index_context(symbol_index: SymbolIndex | None, *, max_entries: int = 10) -> str:
    """Return a compact, bounded trusted repository summary for LLM prompts."""

    if symbol_index is None:
        return ""

    entries = _trusted_symbol_index_entries(symbol_index)
    if not entries:
        return ""

    bullet_lines = "\n".join(f"- {entry}" for entry in entries[:max_entries])
    return (
        "TRUSTED REPOSITORY CONTEXT (generated from repository source, distinct from the untrusted diff below):\n"
        f"{bullet_lines}"
    )


def _trusted_symbol_index_entries(symbol_index: SymbolIndex) -> tuple[str, ...]:
    registration_entries = [
        (
            site[0],
            site[1],
            name,
            symbol_index.invocation_count(name),
            f"'{name}' registered {site[0]}:{site[1]} — "
            f"{_invocation_summary(symbol_index, name)} "
            "repo-wide",
        )
        for name, sites in symbol_index.registrations.items()
        for site in sites
    ]
    if registration_entries:
        return tuple(
            entry[-1]
            for entry in sorted(registration_entries, key=lambda item: (item[3], item[0], item[1], item[2]))
        )

    definition_entries = [
        (
            site[0],
            site[1],
            name,
            site[2],
            f"{site[2]} '{name}' defined {site[0]}:{site[1]} — "
            f"{_invocation_summary(symbol_index, name)} "
            "repo-wide",
        )
        for name, sites in symbol_index.definitions.items()
        for site in sites
    ]
    if definition_entries:
        return tuple(
            entry[-1] for entry in sorted(definition_entries, key=lambda item: (item[0], item[1], item[2], item[3]))
        )

    return tuple(
        f"route {route[1]} {route[2]} at {route[0]}"
        for route in symbol_index.routes
    )


def _invocation_summary(symbol_index: SymbolIndex, name: str) -> str:
    count = symbol_index.invocation_count(name)
    suffix = "" if count == 1 else "s"
    return f"{count} invocation{suffix}"


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
