"""Evaluation metrics for Change Localization benchmark.

Metrics:
  - File Recall: % of actually-changed files the agent found
  - File Precision: % of agent-predicted files that were actually changed
  - File F1: harmonic mean of recall and precision
  - Miss Rate: % of examples where agent missed at least one changed file
  - Exact Match: % of examples where predicted set == actual set
"""
from __future__ import annotations


def file_metrics(predicted: list[str], actual: list[str]) -> dict:
    """Compute per-example file localization metrics."""
    pred_set = set(predicted)
    actual_set = set(actual)

    tp = len(pred_set & actual_set)
    fp = len(pred_set - actual_set)
    fn = len(actual_set - pred_set)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    missed_any = fn > 0
    exact = pred_set == actual_set

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "missed_any": missed_any,
        "exact_match": exact,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "predicted_count": len(pred_set),
        "actual_count": len(actual_set),
        "missed_files": sorted(actual_set - pred_set),
        "extra_files": sorted(pred_set - actual_set),
    }


def aggregate(results: list[dict]) -> dict:
    """Aggregate metrics across all examples."""
    n = len(results)
    if n == 0:
        return {}
    return {
        "n": n,
        "precision": sum(r["precision"] for r in results) / n,
        "recall": sum(r["recall"] for r in results) / n,
        "f1": sum(r["f1"] for r in results) / n,
        "miss_rate": sum(1 for r in results if r["missed_any"]) / n,
        "exact_match": sum(1 for r in results if r["exact_match"]) / n,
        "avg_predicted": sum(r["predicted_count"] for r in results) / n,
        "avg_actual": sum(r["actual_count"] for r in results) / n,
    }
