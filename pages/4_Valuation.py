"""Company Valuation & Fundamentals — ratio analysis, historical percentiles,
and composite scoring.  All data from SEC EDGAR metrics + daily prices
stored in session state.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import math

import pandas as pd
import streamlit as st

from src.splits import detect_splits, normalize_metrics
from src.ttm import compute_ttm_latest, is_flow_metric
from src.ui_helpers import color_score
from src.valuation import (
    SCORE_PRESETS,
    compute_fundamental_score,
    compute_growth,
    compute_historical_ratios,
    compute_peg,
    compute_percentile,
    compute_ratios,
)

st.title("Company Valuation & Fundamentals")

# ── Guard: need symbols + metrics + prices ──────────────────────────────────

symbols = st.session_state.get("symbols", [])
if not symbols:
    st.info("No symbols configured. Go to the main page and enter ticker symbols.")
    st.stop()

metrics_data: dict = st.session_state.get("metrics_data", {})
if not metrics_data:
    st.warning("No fundamental metrics available. Fetch SEC EDGAR data first.")
    st.page_link("pages/1_Metrics.py", label="Go to Metrics", icon="📊")
    st.stop()

price_data_all: dict[str, pd.DataFrame] = st.session_state.get("price_data", {})

symbols_with_data = sorted(s for s in symbols if s in metrics_data)
if not symbols_with_data:
    st.info("No symbols have both metrics data. Fetch metrics first.")
    st.stop()

raw_history: dict = st.session_state.get("metrics_raw_history", {})

# ── Helper: get metric history from session state ───────────────────────────


def _get_history(sym: str, metric_name: str) -> list[dict]:
    return raw_history.get(f"{sym}:{metric_name}", [])


# ── Controls ────────────────────────────────────────────────────────────────

col_preset, col_lookback, col_growth = st.columns([1, 1, 1])

with col_preset:
    preset = st.selectbox(
        "Scoring Preset",
        options=list(SCORE_PRESETS.keys()),
        index=0,
        help=(
            "**Balanced**: Equal-ish weighting across categories. "
            "**Value**: Emphasizes cheap valuation. "
            "**Growth**: Emphasizes revenue/EPS growth. "
            "**Quality**: Emphasizes profitability and financial health."
        ),
    )

with col_lookback:
    pct_lookback = st.selectbox(
        "Percentile Lookback",
        options=["3 Years", "5 Years", "All History"],
        index=1,
    )

with col_growth:
    growth_period = st.selectbox(
        "Growth Period",
        options=[1, 3, 5],
        index=0,
        format_func=lambda x: f"{x} Year{'s' if x > 1 else ''} ({'YoY' if x == 1 else 'CAGR'})",
    )

# ── Preset weights display ─────────────────────────────────────────────────

with st.expander("View scoring weights"):
    weights = SCORE_PRESETS[preset]
    weight_df = pd.DataFrame([
        {"Category": cat.title(), "Weight": f"{w:.0%}"}
        for cat, w in weights.items()
    ])
    st.dataframe(weight_df, use_container_width=True, hide_index=True)

# ── Compute all ratios ─────────────────────────────────────────────────────

_splits_cache: dict[str, list] = {}


def _get_splits(sym: str) -> list:
    if sym not in _splits_cache:
        shares = _get_history(sym, "shares_outstanding")
        eps = _get_history(sym, "eps_diluted")
        _splits_cache[sym] = detect_splits(shares, eps)
    return _splits_cache[sym]


def _get_ttm_value(sym: str, metric_name: str, splits: list) -> float | None:
    history = _get_history(sym, metric_name)
    if not history:
        return None
    history = normalize_metrics(history, splits, metric_name)
    vk = "normalized_value" if splits else "metric_value"
    try:
        ttm_val, _ = compute_ttm_latest(history, value_key=vk)
    except TypeError:
        ttm_val = None
    return ttm_val


all_ratios: dict[str, dict] = {}
all_growth: dict[str, dict] = {}
all_percentiles: dict[str, dict] = {}
all_scores: dict[str, tuple] = {}
missing_prices: list[str] = []

for sym in symbols_with_data:
    # Latest price from price_data
    pdf = price_data_all.get(sym)
    if pdf is None or pdf.empty:
        missing_prices.append(sym)
        continue

    try:
        sorted_pdf = pdf.sort_values("price_date")
        latest_price = float(sorted_pdf.iloc[-1]["adj_close"])
    except (TypeError, ValueError, KeyError):
        missing_prices.append(sym)
        continue

    sym_metrics = metrics_data[sym]
    splits = _get_splits(sym)

    # Build TTM values for flow metrics
    ttm_values: dict[str, float | None] = {}
    for m_name in ("revenue", "net_income", "operating_income", "gross_profit",
                    "eps_basic", "eps_diluted", "capital_expenditures",
                    "dividends_paid", "interest_expense"):
        if m_name in sym_metrics:
            fp = sym_metrics[m_name].get("fiscal_period", "")
            if is_flow_metric(m_name) and fp and fp != "FY":
                ttm_values[m_name] = _get_ttm_value(sym, m_name, splits)
            else:
                try:
                    ttm_values[m_name] = float(sym_metrics[m_name]["metric_value"])
                except (TypeError, ValueError):
                    pass

    # Compute ratios
    ratios = compute_ratios(sym_metrics, latest_price, ttm_metrics=ttm_values)
    all_ratios[sym] = ratios

    # Compute growth
    growth: dict[str, float | None] = {}
    for m_name, g_key in [("revenue", "revenue_growth"), ("eps_diluted", "eps_growth"),
                           ("net_income", "net_income_growth")]:
        history = _get_history(sym, m_name)
        growth[g_key] = compute_growth(history, m_name, lookback_years=growth_period)
    all_growth[sym] = growth

    # PEG
    ratios["peg"] = compute_peg(ratios.get("pe_ttm"), growth.get("eps_growth"))

    # Historical percentiles
    percentiles: dict[str, float | None] = {}
    if pct_lookback == "3 Years":
        pct_start = date.today() - timedelta(days=3 * 365)
    elif pct_lookback == "5 Years":
        pct_start = date.today() - timedelta(days=5 * 365)
    else:
        pct_start = None

    # Build price history list-of-dicts for the percentile engine
    prices_for_pct = []
    if pdf is not None and not pdf.empty:
        for _, row in sorted_pdf.iterrows():
            pd_str = str(row["price_date"])
            if pct_start and pd_str < str(pct_start):
                continue
            prices_for_pct.append({"price_date": pd_str, "adj_close": row["adj_close"]})

    if prices_for_pct:
        metric_hist: dict[str, list[dict]] = {}
        for m_name in ("shares_outstanding", "stockholders_equity", "total_assets",
                        "total_liabilities", "cash_and_equivalents", "revenue",
                        "net_income", "operating_income", "eps_diluted", "eps_basic"):
            rows_for_m = _get_history(sym, m_name)
            if rows_for_m:
                metric_hist[m_name] = sorted(rows_for_m, key=lambda r: str(r.get("period_end", "")))

        hist_ratios = compute_historical_ratios(metric_hist, prices_for_pct)

        for ratio_key in ("pe_ttm", "pb", "ps", "ev_ebitda"):
            hist_values = [r[ratio_key] for r in hist_ratios if r.get(ratio_key) is not None]
            if hist_values:
                ratios[ratio_key] = hist_values[-1]
            percentiles[ratio_key] = compute_percentile(ratios.get(ratio_key), hist_values)

    all_percentiles[sym] = percentiles

    ratios["peg"] = compute_peg(ratios.get("pe_ttm"), growth.get("eps_growth"))

    # Fundamental score
    score, cat_scores = compute_fundamental_score(ratios, percentiles, growth, preset=preset)
    all_scores[sym] = (score, cat_scores)

if missing_prices:
    st.warning(f"No price data for: {', '.join(missing_prices)}.")
    st.page_link("pages/2_Prices.py", label="Fetch prices", icon="📈")

scored_symbols = [s for s in symbols_with_data if s in all_ratios]

if not scored_symbols:
    st.info("No symbols with both metrics and price data.")
    st.stop()

# ── Tabs ────────────────────────────────────────────────────────────────────

val_tab_comp, val_tab_scores, val_tab_pct, val_tab_deep = st.tabs(
    ["Comparison", "Scores", "Percentiles", "Deep Dive"]
)

# ── Tab: Comparison Table ───────────────────────────────────────────────────

RATIO_DISPLAY = [
    ("pe_ttm", "P/E", "{:.1f}", False),
    ("pb", "P/B", "{:.2f}", False),
    ("ps", "P/S", "{:.2f}", False),
    ("ev_ebitda", "EV/EBITDA", "{:.1f}", False),
    ("peg", "PEG", "{:.2f}", False),
    ("earnings_yield", "Earn Yield", "{:.1%}", True),
    ("fcf_yield", "FCF Yield", "{:.1%}", True),
    ("gross_margin", "Gross Mgn", "{:.1%}", True),
    ("operating_margin", "Op Mgn", "{:.1%}", True),
    ("net_margin", "Net Mgn", "{:.1%}", True),
    ("roe", "ROE", "{:.1%}", True),
    ("roa", "ROA", "{:.1%}", True),
    ("debt_to_equity", "D/E", "{:.2f}", False),
    ("current_ratio", "Curr Ratio", "{:.2f}", True),
    ("interest_coverage", "Int Cov", "{:.1f}", True),
    ("dividend_yield", "Div Yield", "{:.1%}", True),
]

comp_rows = []
for sym in scored_symbols:
    ratios = all_ratios[sym]
    growth = all_growth.get(sym, {})
    row: dict = {"Symbol": sym}

    for key, label, fmt, _ in RATIO_DISPLAY:
        val = ratios.get(key)
        if val is not None and not math.isnan(val) and not math.isinf(val):
            try:
                row[label] = fmt.format(val)
            except (ValueError, TypeError):
                row[label] = "—"
        else:
            row[label] = "—"

    for g_key, g_label in [("revenue_growth", "Rev Grw"), ("eps_growth", "EPS Grw"),
                            ("net_income_growth", "NI Grw")]:
        val = growth.get(g_key)
        row[g_label] = f"{val:.1%}" if val is not None else "—"

    comp_rows.append(row)

comp_df = pd.DataFrame(comp_rows)

with val_tab_comp:
    st.subheader("Comparison Table")
    st.dataframe(comp_df, use_container_width=True, hide_index=True)
    growth_label = f"{'YoY' if growth_period == 1 else f'{growth_period}yr CAGR'}"
    st.caption(f"Growth period: {growth_label} · Valuation ratios use TTM earnings where available")

# ── Fundamental Score Ranking ───────────────────────────────────────────────

score_rows = []
for sym in scored_symbols:
    score, cat_scores = all_scores.get(sym, (None, {}))
    row = {
        "Symbol": sym,
        "Composite": round(score, 1) if score is not None else None,
        "Valuation": round(cat_scores.get("valuation", 0), 1) if cat_scores.get("valuation") is not None else None,
        "Profitability": round(cat_scores.get("profitability", 0), 1) if cat_scores.get("profitability") is not None else None,
        "Health": round(cat_scores.get("health", 0), 1) if cat_scores.get("health") is not None else None,
        "Growth": round(cat_scores.get("growth", 0), 1) if cat_scores.get("growth") is not None else None,
    }
    score_rows.append(row)

score_df = pd.DataFrame(score_rows)
score_df = score_df.sort_values("Composite", ascending=False, na_position="last")
score_df.insert(0, "Rank", range(1, len(score_df) + 1))

score_cols = ["Composite", "Valuation", "Profitability", "Health", "Growth"]
styled = score_df.style.map(color_score, subset=score_cols)

with val_tab_scores:
    st.subheader("Fundamental Score")
    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Rank": st.column_config.NumberColumn("Rank", width="small"),
            "Composite": st.column_config.NumberColumn("Composite", format="%.1f"),
        },
    )
    st.caption(
        f"Preset: **{preset}** · "
        f"Valuation scored by inverted percentile vs {pct_lookback.lower()} history · "
        f"Scores: 0-100 (higher = more favorable)"
    )

# ── Valuation Percentiles ──────────────────────────────────────────────────

pct_rows = []
for sym in scored_symbols:
    pcts = all_percentiles.get(sym, {})
    ratios = all_ratios[sym]
    row = {"Symbol": sym}
    for ratio_key, label in [("pe_ttm", "P/E"), ("pb", "P/B"),
                              ("ps", "P/S"), ("ev_ebitda", "EV/EBITDA")]:
        val = ratios.get(ratio_key)
        pct = pcts.get(ratio_key)
        if val is not None and not math.isnan(val) and not math.isinf(val):
            row[f"{label}"] = f"{val:.1f}"
        else:
            row[f"{label}"] = "—"
        row[f"{label} %ile"] = f"{pct:.0f}%" if pct is not None else "—"
    pct_rows.append(row)

with val_tab_pct:
    st.subheader("Valuation Percentiles")
    st.caption(
        "Where each stock's current valuation sits within its own history. "
        "Low percentile = cheap relative to its past."
    )
    if pct_rows:
        pct_df = pd.DataFrame(pct_rows)
        st.dataframe(pct_df, use_container_width=True, hide_index=True)

# ── Single-Symbol Deep Dive ────────────────────────────────────────────────

with val_tab_deep:
    st.subheader("Symbol Deep Dive")

    detail_symbol = st.selectbox("Select symbol", options=scored_symbols, key="val_detail")

    if detail_symbol and detail_symbol in all_ratios:
        ratios = all_ratios[detail_symbol]
        growth = all_growth.get(detail_symbol, {})
        percentiles = all_percentiles.get(detail_symbol, {})
        score, cat_scores = all_scores.get(detail_symbol, (None, {}))

        # Score overview
        cols = st.columns(5)
        score_items = [
            ("Composite", score),
            ("Valuation", cat_scores.get("valuation")),
            ("Profitability", cat_scores.get("profitability")),
            ("Health", cat_scores.get("health")),
            ("Growth", cat_scores.get("growth")),
        ]
        for col, (label, val) in zip(cols, score_items):
            col.metric(label, f"{val:.1f}" if val is not None else "—")

        # Key ratios
        st.markdown("**Valuation**")
        val_cols = st.columns(5)
        val_items = [
            ("P/E", ratios.get("pe_ttm"), "{:.1f}", percentiles.get("pe_ttm")),
            ("P/B", ratios.get("pb"), "{:.2f}", percentiles.get("pb")),
            ("P/S", ratios.get("ps"), "{:.2f}", percentiles.get("ps")),
            ("EV/EBITDA", ratios.get("ev_ebitda"), "{:.1f}", percentiles.get("ev_ebitda")),
            ("PEG", ratios.get("peg"), "{:.2f}", None),
        ]
        for col, (label, val, fmt, pct) in zip(val_cols, val_items):
            if val is not None and not math.isnan(val) and not math.isinf(val):
                display = fmt.format(val)
                delta = f"{pct:.0f}th %ile" if pct is not None else None
            else:
                display = "—"
                delta = None
            col.metric(label, display, delta=delta, delta_color="off")

        st.markdown("**Profitability**")
        prof_cols = st.columns(5)
        prof_items = [
            ("Gross Margin", ratios.get("gross_margin"), "{:.1%}"),
            ("Op Margin", ratios.get("operating_margin"), "{:.1%}"),
            ("Net Margin", ratios.get("net_margin"), "{:.1%}"),
            ("ROE", ratios.get("roe"), "{:.1%}"),
            ("ROA", ratios.get("roa"), "{:.1%}"),
        ]
        for col, (label, val, fmt) in zip(prof_cols, prof_items):
            if val is not None and not math.isnan(val) and not math.isinf(val):
                col.metric(label, fmt.format(val))
            else:
                col.metric(label, "—")

        st.markdown("**Financial Health**")
        health_cols = st.columns(5)
        health_items = [
            ("D/E", ratios.get("debt_to_equity"), "{:.2f}"),
            ("Current Ratio", ratios.get("current_ratio"), "{:.2f}"),
            ("Interest Cov", ratios.get("interest_coverage"), "{:.1f}"),
            ("Cash/Assets", ratios.get("cash_to_assets"), "{:.1%}"),
            ("Div Yield", ratios.get("dividend_yield"), "{:.2%}"),
        ]
        for col, (label, val, fmt) in zip(health_cols, health_items):
            if val is not None and not math.isnan(val) and not math.isinf(val):
                col.metric(label, fmt.format(val))
            else:
                col.metric(label, "—")

        st.markdown("**Growth**")
        growth_cols = st.columns(3)
        growth_items = [
            ("Revenue Growth", growth.get("revenue_growth"), "{:.1%}"),
            ("EPS Growth", growth.get("eps_growth"), "{:.1%}"),
            ("Net Income Growth", growth.get("net_income_growth"), "{:.1%}"),
        ]
        for col, (label, val, fmt) in zip(growth_cols, growth_items):
            if val is not None:
                col.metric(label, fmt.format(val))
            else:
                col.metric(label, "—")

        # Historical valuation chart
        st.divider()
        st.markdown("**Historical Valuation Ratios**")

        hist_ratio_choice = st.selectbox(
            "Ratio to chart",
            options=["pe_ttm", "pb", "ps", "ev_ebitda"],
            format_func=lambda x: {"pe_ttm": "P/E", "pb": "P/B", "ps": "P/S", "ev_ebitda": "EV/EBITDA"}[x],
            key="hist_ratio",
        )

        # Build historical ratio data from session state
        _dd_pdf = price_data_all.get(detail_symbol)
        _dd_hist_data = []
        if _dd_pdf is not None and not _dd_pdf.empty:
            if pct_lookback == "3 Years":
                _dd_hist_start = date.today() - timedelta(days=3 * 365)
            elif pct_lookback == "5 Years":
                _dd_hist_start = date.today() - timedelta(days=5 * 365)
            else:
                _dd_hist_start = None

            _dd_prices = []
            for _, row in _dd_pdf.sort_values("price_date").iterrows():
                pd_str = str(row["price_date"])
                if _dd_hist_start and pd_str < str(_dd_hist_start):
                    continue
                _dd_prices.append({"price_date": pd_str, "adj_close": row["adj_close"]})

            if _dd_prices:
                _dd_metric_hist: dict[str, list[dict]] = {}
                for _m_name in ("shares_outstanding", "stockholders_equity", "total_assets",
                                "total_liabilities", "cash_and_equivalents", "revenue",
                                "net_income", "operating_income", "eps_diluted", "eps_basic"):
                    _m_rows = _get_history(detail_symbol, _m_name)
                    if _m_rows:
                        _dd_metric_hist[_m_name] = sorted(_m_rows, key=lambda r: str(r.get("period_end", "")))
                _dd_hist_data = compute_historical_ratios(_dd_metric_hist, _dd_prices)

        if _dd_hist_data:
            chart_df = pd.DataFrame(_dd_hist_data)
            chart_df["period_end"] = pd.to_datetime(chart_df["period_end"])
            chart_df = chart_df.dropna(subset=[hist_ratio_choice])
            chart_df = chart_df.sort_values("period_end")

            if not chart_df.empty:
                st.line_chart(chart_df, x="period_end", y=hist_ratio_choice)

                values = chart_df[hist_ratio_choice].tolist()
                current = values[-1] if values else None
                if current is not None and len(values) >= 4:
                    mn, mx, avg = min(values), max(values), sum(values) / len(values)
                    pct = compute_percentile(current, values)
                    range_cols = st.columns(4)
                    range_cols[0].metric("Current", f"{current:.2f}")
                    range_cols[1].metric("Avg", f"{avg:.2f}")
                    range_cols[2].metric("Range", f"{mn:.1f} — {mx:.1f}")
                    range_cols[3].metric("Percentile", f"{pct:.0f}%" if pct is not None else "—")
            else:
                st.info("Not enough historical data points to chart this ratio.")
        else:
            st.info("No price history available. Fetch prices first.")
