"""Recall-gap mining helpers for missed benchmark cases and unmatched golden findings."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from eval.golden import GoldenFinding
from eval.score import load_cases

_STOPWORDS = {
    "a",
    "an",
    "and",
    "case",
    "defined",
    "finding",
    "high",
    "never",
    "no",
    "not",
    "of",
    "service",
    "the",
    "to",
    "bootstrap",
    "invoked",
    "called",
    "but",
    "app",
    "src",
    "py",
}


@dataclass(frozen=True, slots=True)
class MiningSignal:
    path: str
    title: str
    category: str | None = None

    @property
    def signature(self) -> str:
        return f"{self.path} {self.title}".strip().lower()


@dataclass(frozen=True, slots=True)
class MiningCluster:
    signals: tuple[MiningSignal, ...]
    suggested_lens: str

    @property
    def cluster_size(self) -> int:
        return len(self.signals)

    @property
    def representative_titles(self) -> list[str]:
        titles = sorted({signal.title for signal in self.signals})
        return titles[:3]


def signals_from_case_misses(miss_ids: list[str], benchmark_root: Path) -> list[MiningSignal]:
    case_map = {(case.category, case.id): (case, folder) for case, folder in load_cases(benchmark_root)}
    signals: list[MiningSignal] = []
    if not miss_ids:
        return signals
    categories = sorted(
        {
            case.category
            for case, _folder in load_cases(benchmark_root)
            if case.expected_behavior != "ignore" and case.category != "clean"
        }
    )
    for miss_id in miss_ids:
        for category in categories:
            entry = case_map.get((category, miss_id))
            if entry is None:
                continue
            case, folder = entry
            filename = "code.py" if case.category == "clean" else "defect.py"
            signals.append(
                MiningSignal(
                    path=(folder / filename).as_posix(),
                    title=case.description,
                    category=case.category,
                )
            )
    return signals


def signals_from_unmatched_golden(findings: list[GoldenFinding]) -> list[MiningSignal]:
    return [MiningSignal(path=finding.path, title=finding.title) for finding in findings]


def cluster_signals(signals: list[MiningSignal], min_similarity: float = 0.45) -> list[MiningCluster]:
    clusters: list[list[MiningSignal]] = []
    for signal in sorted(signals, key=lambda item: (item.path, item.title, item.category or "")):
        matched_cluster = next(
            (cluster for cluster in clusters if _cluster_similarity(signal, cluster) >= min_similarity),
            None,
        )
        if matched_cluster is None:
            clusters.append([signal])
            continue
        matched_cluster.append(signal)

    ranked = sorted(
        clusters,
        key=lambda cluster: (-len(cluster), cluster[0].path, cluster[0].title),
    )
    return [MiningCluster(signals=tuple(cluster), suggested_lens=_suggest_lens(cluster)) for cluster in ranked]


def render_cluster_markdown(clusters: list[MiningCluster]) -> str:
    lines = [
        "| cluster size | representative titles | suggested lens name |",
        "|---|---|---|",
    ]
    for cluster in clusters:
        titles = "; ".join(cluster.representative_titles)
        lines.append(f"| {cluster.cluster_size} | {titles} | {cluster.suggested_lens} |")
    return "\n".join(lines)


def _cluster_similarity(signal: MiningSignal, cluster: list[MiningSignal]) -> float:
    return max(_signal_similarity(signal, existing) for existing in cluster)


def _signal_similarity(left: MiningSignal, right: MiningSignal) -> float:
    left_tokens = _tokens(left.signature)
    right_tokens = _tokens(right.signature)
    union = left_tokens | right_tokens
    jaccard = 1.0 if not union else len(left_tokens & right_tokens) / len(union)
    sequence = SequenceMatcher(a=left.signature, b=right.signature).ratio()
    return max(jaccard, sequence)


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]+", value.lower()) if token not in _STOPWORDS}


def _suggest_lens(cluster: list[MiningSignal]) -> str:
    categories = [signal.category for signal in cluster if signal.category]
    if categories:
        most_common_category, _ = Counter(categories).most_common(1)[0]
        return f"{most_common_category}_followup"

    token_counts = Counter(token for signal in cluster for token in _tokens(signal.signature))
    if not token_counts:
        return "general_followup"
    token, _ = sorted(token_counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))[0]
    return f"{token}_followup"
