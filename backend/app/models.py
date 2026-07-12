from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class DimStock(Base):
    __tablename__ = "dim_stock"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(16))
    industry: Mapped[str | None] = mapped_column(String(64))
    list_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TradeCalendar(Base):
    __tablename__ = "trade_calendar"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16), primary_key=True, default="SSE")
    is_open: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DailyBar(Base):
    __tablename__ = "daily_bar"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_daily_bar_code_date"),
        Index("ix_daily_bar_trade_date_code", "trade_date", "code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    pct_chg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DailyBarAdj(Base):
    """Stable research prices: raw OHLC multiplied by that day's adjustment factor."""

    __tablename__ = "daily_bar_adj"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_daily_bar_adj_code_date"),
        Index("ix_daily_bar_adj_trade_date_code", "trade_date", "code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    pct_chg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AdjFactor(Base):
    __tablename__ = "adj_factor"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_adj_factor_code_date"),
        Index("ix_adj_factor_trade_date_code", "trade_date", "code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    adj_factor: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DailyBasic(Base):
    __tablename__ = "daily_basic"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_daily_basic_code_date"),
        Index("ix_daily_basic_trade_date_code", "trade_date", "code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    pe_ttm: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    pb: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    ps_ttm: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    total_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    float_mv: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    is_st: Mapped[int] = mapped_column(Integer, default=0)
    is_suspended: Mapped[int] = mapped_column(Integer, default=0)
    limit_status: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DataSyncState(Base):
    __tablename__ = "data_sync_state"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_trade_date: Mapped[date | None] = mapped_column(Date)
    last_row_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    error_message: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class FactorDaily(Base):
    __tablename__ = "factor_daily"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", "factor_name", name="uq_factor_daily"),
        Index("ix_factor_daily_date_name", "trade_date", "factor_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    factor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    factor_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    factor_zscore: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ModelPrediction(Base):
    __tablename__ = "model_prediction"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", "horizon", "model_version", name="uq_model_prediction"),
        Index("ix_model_prediction_rank", "trade_date", "horizon", "score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False, default="rule-v1")
    score: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    probability: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    rank_no: Mapped[int | None] = mapped_column(Integer)
    factor_snapshot: Mapped[str | None] = mapped_column(Text)
    risk_flags: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BacktestSummary(Base):
    __tablename__ = "backtest_summary"
    __table_args__ = (
        UniqueConstraint("horizon", "model_version", "start_date", "end_date", name="uq_backtest_summary"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False, default="rule-v1")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    top_group_return: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    benchmark_return: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    sharpe: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    rank_ic: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
