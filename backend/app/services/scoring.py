from __future__ import annotations

import math
from typing import Any


FACTOR_WEIGHTS: dict[str, float] = {
    "momentum_20d": 0.22,
    "momentum_60d": 0.18,
    "trend_strength": 0.16,
    "reversal_5d": 0.08,
    "volatility_20d": -0.12,
    "drawdown_60d": -0.10,
    "turnover_rate": 0.08,
    "valuation": 0.10,
    "quality": 0.16,
}


def sigmoid(value: float) -> float:
    value = max(min(value, 12.0), -12.0)
    return 1.0 / (1.0 + math.exp(-value))


def weighted_score(factors: dict[str, float]) -> float:
    raw = 0.0
    total_weight = 0.0
    for factor_name, weight in FACTOR_WEIGHTS.items():
        if factor_name not in factors:
            continue
        raw += float(factors[factor_name]) * weight
        total_weight += abs(weight)
    if total_weight == 0:
        return 0.0
    return raw / total_weight


def probability_from_score(score: float, horizon: str) -> float:
    horizon_bias = {
        "5d": 0.05,
        "20d": 0.0,
        "60d": -0.03,
    }.get(horizon, 0.0)
    scaled = score * 2.4 + horizon_bias
    probability = sigmoid(scaled)
    probability = max(min(probability, 0.9999), 0.0001)
    return round(probability, 4)


def factor_contributions(factors: dict[str, float]) -> list[dict[str, Any]]:
    contributions = []
    for factor_name, weight in FACTOR_WEIGHTS.items():
        value = float(factors.get(factor_name, 0.0))
        contributions.append(
            {
                "name": factor_name,
                "value": round(value, 4),
                "weight": weight,
                "contribution": round(value * weight, 4),
            }
        )
    return sorted(contributions, key=lambda row: abs(row["contribution"]), reverse=True)


def risk_flags(factors: dict[str, float]) -> list[str]:
    flags: list[str] = []
    if factors.get("volatility_20d", 0) > 0.85:
        flags.append("20日波动偏高")
    if factors.get("drawdown_60d", 0) > 0.75:
        flags.append("60日回撤偏大")
    if factors.get("turnover_rate", 0) < -0.65:
        flags.append("流动性偏弱")
    if factors.get("valuation", 0) < -0.75:
        flags.append("估值因子偏弱")
    return flags


def build_prediction(
    stock: dict[str, Any],
    factors: dict[str, float],
    horizon: str,
    rank_no: int | None = None,
) -> dict[str, Any]:
    score = round(weighted_score(factors), 4)
    probability = probability_from_score(score, horizon)
    return {
        "code": stock["code"],
        "name": stock["name"],
        "industry": stock.get("industry", "未分类"),
        "trade_date": stock.get("trade_date"),
        "last_close": stock.get("last_close"),
        "horizon": horizon,
        "score": score,
        "probability": probability,
        "rank": rank_no,
        "factor_highlights": factor_contributions(factors)[:4],
        "factor_snapshot": factors,
        "risk_flags": risk_flags(factors),
    }
