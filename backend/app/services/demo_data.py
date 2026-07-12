from __future__ import annotations

from datetime import date
from typing import Any

from .scoring import build_prediction


DEMO_TRADE_DATE = date(2026, 7, 7).isoformat()


DEMO_STOCKS: list[dict[str, Any]] = [
    {
        "code": "600519",
        "name": "贵州茅台",
        "industry": "食品饮料",
        "last_close": 1488.20,
        "factors": {
            "momentum_20d": 0.42,
            "momentum_60d": 0.37,
            "trend_strength": 0.54,
            "reversal_5d": -0.18,
            "volatility_20d": -0.28,
            "drawdown_60d": -0.34,
            "turnover_rate": -0.22,
            "valuation": 0.18,
            "quality": 0.88,
        },
    },
    {
        "code": "000858",
        "name": "五粮液",
        "industry": "食品饮料",
        "last_close": 132.64,
        "factors": {
            "momentum_20d": 0.35,
            "momentum_60d": 0.18,
            "trend_strength": 0.32,
            "reversal_5d": 0.08,
            "volatility_20d": -0.16,
            "drawdown_60d": -0.28,
            "turnover_rate": 0.05,
            "valuation": 0.25,
            "quality": 0.72,
        },
    },
    {
        "code": "300750",
        "name": "宁德时代",
        "industry": "电力设备",
        "last_close": 214.58,
        "factors": {
            "momentum_20d": 0.66,
            "momentum_60d": 0.58,
            "trend_strength": 0.61,
            "reversal_5d": -0.12,
            "volatility_20d": 0.42,
            "drawdown_60d": 0.12,
            "turnover_rate": 0.38,
            "valuation": -0.18,
            "quality": 0.54,
        },
    },
    {
        "code": "601318",
        "name": "中国平安",
        "industry": "非银金融",
        "last_close": 45.18,
        "factors": {
            "momentum_20d": 0.21,
            "momentum_60d": 0.27,
            "trend_strength": 0.24,
            "reversal_5d": 0.12,
            "volatility_20d": -0.22,
            "drawdown_60d": -0.18,
            "turnover_rate": 0.18,
            "valuation": 0.48,
            "quality": 0.36,
        },
    },
    {
        "code": "600036",
        "name": "招商银行",
        "industry": "银行",
        "last_close": 37.52,
        "factors": {
            "momentum_20d": 0.28,
            "momentum_60d": 0.31,
            "trend_strength": 0.29,
            "reversal_5d": 0.04,
            "volatility_20d": -0.36,
            "drawdown_60d": -0.26,
            "turnover_rate": -0.08,
            "valuation": 0.52,
            "quality": 0.46,
        },
    },
    {
        "code": "002415",
        "name": "海康威视",
        "industry": "计算机",
        "last_close": 31.16,
        "factors": {
            "momentum_20d": 0.18,
            "momentum_60d": 0.22,
            "trend_strength": 0.26,
            "reversal_5d": 0.16,
            "volatility_20d": 0.06,
            "drawdown_60d": -0.12,
            "turnover_rate": 0.12,
            "valuation": 0.34,
            "quality": 0.41,
        },
    },
    {
        "code": "600276",
        "name": "恒瑞医药",
        "industry": "医药生物",
        "last_close": 43.85,
        "factors": {
            "momentum_20d": 0.24,
            "momentum_60d": 0.10,
            "trend_strength": 0.20,
            "reversal_5d": -0.04,
            "volatility_20d": 0.08,
            "drawdown_60d": -0.10,
            "turnover_rate": 0.20,
            "valuation": -0.10,
            "quality": 0.62,
        },
    },
    {
        "code": "601012",
        "name": "隆基绿能",
        "industry": "电力设备",
        "last_close": 18.44,
        "factors": {
            "momentum_20d": 0.44,
            "momentum_60d": -0.04,
            "trend_strength": 0.08,
            "reversal_5d": 0.35,
            "volatility_20d": 0.78,
            "drawdown_60d": 0.65,
            "turnover_rate": 0.62,
            "valuation": 0.08,
            "quality": -0.18,
        },
    },
    {
        "code": "002594",
        "name": "比亚迪",
        "industry": "汽车",
        "last_close": 253.40,
        "factors": {
            "momentum_20d": 0.52,
            "momentum_60d": 0.35,
            "trend_strength": 0.48,
            "reversal_5d": -0.22,
            "volatility_20d": 0.36,
            "drawdown_60d": 0.10,
            "turnover_rate": 0.31,
            "valuation": -0.24,
            "quality": 0.50,
        },
    },
    {
        "code": "000333",
        "name": "美的集团",
        "industry": "家用电器",
        "last_close": 69.70,
        "factors": {
            "momentum_20d": 0.31,
            "momentum_60d": 0.30,
            "trend_strength": 0.34,
            "reversal_5d": 0.02,
            "volatility_20d": -0.20,
            "drawdown_60d": -0.16,
            "turnover_rate": -0.04,
            "valuation": 0.38,
            "quality": 0.58,
        },
    },
    {
        "code": "600030",
        "name": "中信证券",
        "industry": "非银金融",
        "last_close": 22.18,
        "factors": {
            "momentum_20d": 0.38,
            "momentum_60d": 0.26,
            "trend_strength": 0.31,
            "reversal_5d": -0.08,
            "volatility_20d": 0.18,
            "drawdown_60d": -0.04,
            "turnover_rate": 0.46,
            "valuation": 0.16,
            "quality": 0.12,
        },
    },
    {
        "code": "688981",
        "name": "中芯国际",
        "industry": "电子",
        "last_close": 76.25,
        "factors": {
            "momentum_20d": 0.59,
            "momentum_60d": 0.44,
            "trend_strength": 0.55,
            "reversal_5d": -0.20,
            "volatility_20d": 0.72,
            "drawdown_60d": 0.22,
            "turnover_rate": 0.58,
            "valuation": -0.52,
            "quality": 0.22,
        },
    },
]


def demo_recommendations(horizon: str = "20d", limit: int = 20, min_score: float | None = None) -> list[dict[str, Any]]:
    predictions = []
    for stock in DEMO_STOCKS:
        base = {
            "code": stock["code"],
            "name": stock["name"],
            "industry": stock["industry"],
            "last_close": stock["last_close"],
            "trade_date": DEMO_TRADE_DATE,
        }
        predictions.append(build_prediction(base, stock["factors"], horizon))

    predictions.sort(key=lambda row: row["score"], reverse=True)
    for index, prediction in enumerate(predictions, start=1):
        prediction["rank"] = index
    if min_score is not None:
        predictions = [row for row in predictions if row["score"] >= min_score]
    return predictions[:limit]


def demo_explanation(code: str, horizon: str = "20d") -> dict[str, Any] | None:
    predictions = demo_recommendations(horizon=horizon, limit=len(DEMO_STOCKS))
    for row in predictions:
        if row["code"] == code:
            return {
                "prediction": row,
                "method": "rule-v1",
                "notes": [
                    "该解释来自 demo 数据，真实运行时会读取 MySQL 中的因子和模型结果。",
                    "概率是排序参考，不是确定性预测。",
                ],
            }
    return None


def demo_backtest_summary(horizon: str = "20d") -> dict[str, Any]:
    horizon_profiles = {
        "5d": {
            "top_group_return": 0.018,
            "benchmark_return": 0.009,
            "win_rate": 0.548,
            "max_drawdown": -0.062,
            "sharpe": 1.18,
            "rank_ic": 0.041,
            "turnover": 0.42,
        },
        "20d": {
            "top_group_return": 0.064,
            "benchmark_return": 0.031,
            "win_rate": 0.572,
            "max_drawdown": -0.118,
            "sharpe": 1.36,
            "rank_ic": 0.057,
            "turnover": 0.31,
        },
        "60d": {
            "top_group_return": 0.128,
            "benchmark_return": 0.073,
            "win_rate": 0.586,
            "max_drawdown": -0.164,
            "sharpe": 1.24,
            "rank_ic": 0.049,
            "turnover": 0.18,
        },
    }
    metrics = horizon_profiles.get(horizon, horizon_profiles["20d"])
    return {
        "horizon": horizon,
        "model_version": "rule-v1-demo",
        "start_date": "2023-01-01",
        "end_date": DEMO_TRADE_DATE,
        "notes": "Demo metrics. Replace with stored rolling backtest output after MySQL data sync.",
        **metrics,
    }

