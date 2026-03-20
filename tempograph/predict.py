"""Session-based next-mode prediction for speculative prefetching.

Learns transition probabilities from usage.jsonl and predicts what mode
the agent will likely call next. Used to pre-warm results or suggest
the next tool to call.

This is the v1 Markov chain predictor. Future: GRU4Rec for sequence modeling.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


def build_transition_matrix(repo: str, min_count: int = 5) -> dict[str, list[tuple[str, float]]]:
    """Build a first-order Markov transition matrix from usage.jsonl.

    Returns {mode: [(next_mode, probability), ...]} sorted by probability desc.
    Only includes transitions seen >= min_count times.
    """
    usage_path = Path(repo) / ".tempograph" / "usage.jsonl"
    if not usage_path.exists():
        return {}

    # Extract mode sequence
    modes: list[str] = []
    try:
        with open(usage_path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    mode = r.get("mode") or r.get("tool", "")
                    if mode:
                        modes.append(mode)
                except (json.JSONDecodeError, KeyError):
                    continue
    except OSError:
        return {}

    if len(modes) < 2:
        return {}

    # Count bigrams
    bigrams: Counter[tuple[str, str]] = Counter()
    unigrams: Counter[str] = Counter()
    for i in range(len(modes) - 1):
        # Skip self-transitions (same mode called repeatedly = retries, not patterns)
        if modes[i] != modes[i + 1]:
            bigrams[(modes[i], modes[i + 1])] += 1
            unigrams[modes[i]] += 1

    # Build transition probabilities
    matrix: dict[str, list[tuple[str, float]]] = {}
    for (src, dst), count in bigrams.items():
        if count < min_count:
            continue
        prob = count / unigrams[src]
        matrix.setdefault(src, []).append((dst, prob))

    # Sort each source's transitions by probability desc
    for src in matrix:
        matrix[src] = sorted(matrix[src], key=lambda x: -x[1])

    return matrix


def predict_next(repo: str, current_mode: str, top_k: int = 3) -> list[tuple[str, float]]:
    """Predict the most likely next mode(s) given the current mode.

    Returns [(next_mode, probability), ...] up to top_k results.
    """
    matrix = build_transition_matrix(repo)
    return matrix.get(current_mode, [])[:top_k]


def suggest_prefetch(repo: str, current_mode: str, threshold: float = 0.2) -> list[str]:
    """Suggest modes to pre-warm based on transition probability.

    Only suggests modes with >= threshold probability.
    """
    predictions = predict_next(repo, current_mode, top_k=5)
    return [mode for mode, prob in predictions if prob >= threshold]
