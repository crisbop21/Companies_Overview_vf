import logging
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)


# ── Stock metrics ────────────────────────────────────────────────────────────

KNOWN_METRICS = {
    "revenue",
    "net_income",
    "eps_basic",
    "eps_diluted",
    "total_assets",
    "total_liabilities",
    "stockholders_equity",
    "shares_outstanding",
    "operating_income",
    "cash_and_equivalents",
    "gross_profit",
    "current_assets",
    "current_liabilities",
    "long_term_debt",
    "capital_expenditures",
    "dividends_paid",
    "interest_expense",
}


VALID_REPORTING_STYLES = {"cumulative_ytd", "standalone_quarterly", "annual_only", "mixed"}


class StockMetric(BaseModel):
    """A single fundamental metric for a stock, sourced from SEC EDGAR."""

    symbol: str
    metric_name: str
    metric_value: Decimal
    period_end: date
    period_start: date | None = None       # start of reporting period (for duration calc)
    fiscal_period: str | None = None       # 'FY', 'Q1', 'Q2', 'Q3', 'Q4'
    fiscal_year: int | None = None         # from XBRL 'fy' field (reliable for non-calendar FY)
    duration_days: int | None = None       # (end - start).days — distinguishes YTD from standalone
    reporting_style: str | None = None     # cumulative_ytd | standalone_quarterly | annual_only | mixed
    source: str = "SEC_EDGAR"
    cik: str | None = None
    filing_type: str | None = None

    @field_validator("symbol")
    @classmethod
    def symbol_not_blank(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("symbol must not be blank")
        return v

    @field_validator("metric_name")
    @classmethod
    def metric_name_known(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in KNOWN_METRICS:
            logger.warning("Metric '%s' not in KNOWN_METRICS — allowing but verify", v)
        return v

    @model_validator(mode="after")
    def check_source_has_cik(self):
        if self.source == "SEC_EDGAR" and not self.cik:
            raise ValueError(
                f"SEC_EDGAR metric '{self.metric_name}' for {self.symbol} "
                f"must include a CIK for traceability."
            )
        return self


# ── Daily prices ─────────────────────────────────────────────────────────────


class DailyPrice(BaseModel):
    """One day of OHLCV price data for a symbol, sourced from Yahoo Finance."""

    symbol: str
    price_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int

    @field_validator("symbol")
    @classmethod
    def symbol_not_blank(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("symbol must not be blank")
        return v
