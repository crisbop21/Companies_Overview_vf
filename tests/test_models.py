from datetime import date
from decimal import Decimal

import pytest

from src.models import StockMetric


class TestStockMetric:
    def test_valid_metric(self):
        m = StockMetric(
            symbol="AAPL",
            metric_name="revenue",
            metric_value=Decimal("394328000000"),
            period_end=date(2024, 9, 28),
            cik="0000320193",
        )
        assert m.symbol == "AAPL"
        assert m.metric_name == "revenue"
        assert m.source == "SEC_EDGAR"

    def test_symbol_normalised_to_uppercase(self):
        m = StockMetric(
            symbol="aapl",
            metric_name="revenue",
            metric_value=Decimal("100"),
            period_end=date(2024, 1, 1),
            cik="0000320193",
        )
        assert m.symbol == "AAPL"

    def test_blank_symbol_rejected(self):
        with pytest.raises(ValueError, match="must not be blank"):
            StockMetric(
                symbol="  ",
                metric_name="revenue",
                metric_value=Decimal("100"),
                period_end=date(2024, 1, 1),
                cik="0000320193",
            )

    def test_sec_edgar_requires_cik(self):
        with pytest.raises(ValueError, match="must include a CIK"):
            StockMetric(
                symbol="AAPL",
                metric_name="revenue",
                metric_value=Decimal("100"),
                period_end=date(2024, 1, 1),
                source="SEC_EDGAR",
                cik=None,
            )

    def test_non_sec_source_allows_no_cik(self):
        m = StockMetric(
            symbol="AAPL",
            metric_name="revenue",
            metric_value=Decimal("100"),
            period_end=date(2024, 1, 1),
            source="MANUAL",
            cik=None,
        )
        assert m.cik is None

    def test_decimal_precision_preserved(self):
        m = StockMetric(
            symbol="MSFT",
            metric_name="eps_diluted",
            metric_value=Decimal("11.86"),
            period_end=date(2024, 6, 30),
            cik="0000789019",
        )
        assert m.metric_value == Decimal("11.86")
