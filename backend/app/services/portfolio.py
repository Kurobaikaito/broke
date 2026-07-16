from __future__ import annotations

import math
from typing import Any, Iterable


MIN_CAPITAL = 10_000.0
MAX_CAPITAL = 100_000.0
DEFAULT_CAPITAL = 50_000.0
LOT_SIZE = 100
CASH_BUFFER_RATE = 0.03


def validate_capital(capital: float) -> float:
    value = float(capital)
    if not MIN_CAPITAL <= value <= MAX_CAPITAL:
        raise ValueError(f"capital must be between {MIN_CAPITAL:.0f} and {MAX_CAPITAL:.0f}")
    return value


def target_position_count(capital: float, enforce_input_range: bool = True) -> int:
    """Scale a small account from three to at most ten positions."""
    value = validate_capital(capital) if enforce_input_range else float(capital)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("capital must be positive")
    return max(3, min(10, int(value // 10_000) + 2))


def allocate_lot_positions(
    candidates: Iterable[dict[str, Any]],
    capital: float,
    price_key: str = "last_close",
    cash_buffer_rate: float = CASH_BUFFER_RATE,
    max_positions: int | None = None,
    enforce_input_range: bool = True,
) -> list[dict[str, Any]]:
    """Select affordable ranked stocks and size equal-cash positions in whole lots."""
    value = validate_capital(capital) if enforce_input_range else float(capital)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("capital must be positive")
    if not 0 <= cash_buffer_rate < 1:
        raise ValueError("cash_buffer_rate must be in [0, 1)")
    target_count = target_position_count(value, enforce_input_range=enforce_input_range)
    if max_positions is not None:
        if max_positions <= 0:
            raise ValueError("max_positions must be positive")
        target_count = min(target_count, max_positions)
    per_position_budget = value * (1.0 - cash_buffer_rate) / target_count
    positions: list[dict[str, Any]] = []

    for candidate in candidates:
        raw_price = candidate.get(price_key)
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0:
            continue
        shares = int(per_position_budget // (price * LOT_SIZE)) * LOT_SIZE
        if shares < LOT_SIZE:
            continue
        amount = shares * price
        position = dict(candidate)
        position["model_rank"] = candidate.get("rank")
        position["rank"] = len(positions) + 1
        position["target_shares"] = shares
        position["target_amount"] = round(amount, 2)
        position["target_weight"] = round(amount / value, 6)
        positions.append(position)
        if len(positions) >= target_count:
            break
    return positions
