"""Pure binary-relevance ranking metrics and descriptive timing statistics."""
import math


def ranking_metrics(ranked_ids, relevant_ids):
    """Macro-average these per-query values; duplicate results are invalid."""
    k = 5
    if not relevant_ids:
        raise ValueError("Metrics require at least one relevant chunk")
    if len(ranked_ids) != len(set(ranked_ids)):
        raise ValueError("Duplicate ranked chunk IDs")
    gold = set(relevant_ids)
    hits = [int(chunk_id in gold) for chunk_id in ranked_ids[:k]]
    first = next((rank for rank, hit in enumerate(hits, 1) if hit), None)
    dcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(hits, 1))
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(gold), k) + 1))
    return {"hit_at_1": float(bool(hits and hits[0])), "hit_at_5": float(any(hits)),
            "mrr_at_5": 1 / first if first else 0.0,
            "recall_at_5": sum(hits) / len(gold), "ndcg_at_5": dcg / ideal}


def timing_summary(values):
    """Linear-interpolated percentiles at (n-1)*p; milliseconds throughout."""
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Timing samples must be finite, non-negative and non-empty")
    ordered = sorted(values)
    def percentile(p):
        position = (len(ordered) - 1) * p
        lo = math.floor(position)
        hi = math.ceil(position)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)
    return {"sample_count": len(values), "min_ms": ordered[0], "max_ms": ordered[-1],
            "mean_ms": sum(values) / len(values), "p50_ms": percentile(.5),
            "p95_ms": percentile(.95), "p99_ms": percentile(.99)}
