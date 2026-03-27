"""Session-based next-mode prediction for speculative prefetching.

Learns transition probabilities from usage.jsonl and predicts what mode
the agent will likely call next. Used to pre-warm results or suggest
the next tool to call.

v1: first-order Markov chain (current_mode -> next_mode)
v2: second-order Markov chain (prev_mode, current_mode) -> next_mode
    Evidence on tempograph's own usage: +26% to +81% prediction confidence
    vs first-order on the same transitions (real 7987-event dataset).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def _load_modes(repo: str) -> list[str]:
    """Load mode sequence from usage.jsonl. Returns [] on any failure."""
    usage_path = Path(repo) / ".tempograph" / "usage.jsonl"
    if not usage_path.exists():
        return []
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
        return []
    return modes


def build_transition_matrix(repo: str, min_count: int = 5) -> dict[str, list[tuple[str, float]]]:
    """Build a first-order Markov transition matrix from usage.jsonl.

    Returns {mode: [(next_mode, probability), ...]} sorted by probability desc.
    Only includes transitions seen >= min_count times.
    """
    modes = _load_modes(repo)
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


def build_transition_matrix_2nd(
    repo: str, min_count: int = 3
) -> dict[tuple[str, str], list[tuple[str, float]]]:
    """Build a second-order Markov transition matrix from usage.jsonl.

    Uses (prev_mode, current_mode) pairs as state to predict next_mode.
    Lower default min_count than first-order because trigrams are rarer.

    Returns {(prev_mode, curr_mode): [(next_mode, probability), ...]} sorted by prob desc.

    Evidence: on tempograph's 7987-event usage log, second-order is +26% to +81% more
    confident than first-order on the same transitions — e.g.
      (hotspots, blast_radius) -> diff_context: 100% vs 33% (first-order)
      (stats, focus)           -> lookup:       100% vs 19% (first-order)
    Context collapses uncertainty that single-mode state cannot resolve.
    """
    modes = _load_modes(repo)
    if len(modes) < 3:
        return {}

    # Count trigrams (prev, curr, next) skipping self-transitions at each step
    trigrams: Counter[tuple[str, str, str]] = Counter()
    bigrams2: Counter[tuple[str, str]] = Counter()
    for i in range(len(modes) - 2):
        a, b, c = modes[i], modes[i + 1], modes[i + 2]
        if a != b and b != c:
            trigrams[(a, b, c)] += 1
            bigrams2[(a, b)] += 1

    # Build transition probabilities
    matrix: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for (a, b, c), count in trigrams.items():
        if count < min_count:
            continue
        prob = count / bigrams2[(a, b)]
        matrix.setdefault((a, b), []).append((c, prob))

    for key in matrix:
        matrix[key] = sorted(matrix[key], key=lambda x: -x[1])

    return matrix


def predict_next(repo: str, current_mode: str, top_k: int = 3) -> list[tuple[str, float]]:
    """Predict the most likely next mode(s) given the current mode (first-order).

    Returns [(next_mode, probability), ...] up to top_k results.
    """
    matrix = build_transition_matrix(repo)
    return matrix.get(current_mode, [])[:top_k]


def predict_next_2nd(
    repo: str, prev_mode: str, current_mode: str, top_k: int = 3
) -> list[tuple[str, float]]:
    """Predict next mode using second-order Markov (prev_mode, current_mode) -> next.

    Falls back to first-order predict_next when no second-order data exists for
    this (prev, curr) pair — e.g. early in a session or novel transition sequences.

    Returns [(next_mode, probability), ...] up to top_k results.
    """
    matrix_2nd = build_transition_matrix_2nd(repo)
    results = matrix_2nd.get((prev_mode, current_mode), [])[:top_k]
    if results:
        return results
    return predict_next(repo, current_mode, top_k=top_k)


def suggest_prefetch(
    repo: str, current_mode: str, threshold: float = 0.2, prev_mode: str = ""
) -> list[str]:
    """Suggest modes to pre-warm based on transition probability.

    Uses second-order prediction when prev_mode is provided, otherwise first-order.
    Only suggests modes with >= threshold probability.
    """
    if prev_mode:
        predictions = predict_next_2nd(repo, prev_mode, current_mode, top_k=5)
    else:
        predictions = predict_next(repo, current_mode, top_k=5)
    return [mode for mode, prob in predictions if prob >= threshold]
