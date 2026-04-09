"""Prices — fetch and view daily stock prices from Yahoo Finance."""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.price_fetcher import fetch_daily_prices
from src.ui_helpers import COLORS

st.title("Daily Prices")

# ── Guard: need symbols ─────────────────────────────────────────────────────

symbols = st.session_state.get("symbols", [])
if not symbols:
    st.info("No symbols configured. Go to the main page and enter ticker symbols.")
    st.stop()

# Always include benchmarks for technical/beta analysis
BENCHMARK_SYMBOLS = ["SPY", "QQQ"]
benchmarks_to_add = [b for b in BENCHMARK_SYMBOLS if b not in symbols]
symbols_with_benchmarks = symbols + benchmarks_to_add

# ── Fetch controls ──────────────────────────────────────────────────────────

st.subheader("Fetch Prices")

col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("Start date", value=date.today() - timedelta(days=365))
with col2:
    end_date = st.date_input("End date", value=date.today())

st.write(f"**Symbols:** {', '.join(symbols)}")
if benchmarks_to_add:
    st.caption(f"Benchmarks included: {', '.join(benchmarks_to_add)}")

if st.button("Fetch All Prices", type="primary"):
    fetch_list = symbols_with_benchmarks
    progress = st.progress(0, text="Fetching prices...")
    all_price_data: dict[str, pd.DataFrame] = st.session_state.get("price_data", {})
    all_errors = []

    for i, sym in enumerate(fetch_list):
        progress.progress(
            (i + 1) / len(fetch_list),
            text=f"Fetching {sym} ({i + 1}/{len(fetch_list)})...",
        )
        prices, errs = fetch_daily_prices(sym, start=start_date, end=end_date)
        all_errors.extend(errs)

        if prices:
            rows = [
                {
                    "symbol": p.symbol,
                    "price_date": str(p.price_date),
                    "open": float(p.open),
                    "high": float(p.high),
                    "low": float(p.low),
                    "close": float(p.close),
                    "adj_close": float(p.adj_close),
                    "volume": p.volume,
                }
                for p in prices
            ]
            all_price_data[sym] = pd.DataFrame(rows)

    progress.empty()

    if all_errors:
        with st.expander(f"{len(all_errors)} warnings/errors"):
            for err in all_errors:
                st.warning(err)

    st.session_state["price_data"] = all_price_data
    fetched_count = sum(1 for sym in fetch_list if sym in all_price_data)
    st.success(f"Fetched price data for {fetched_count}/{len(fetch_list)} symbols.")

st.divider()

# ── Single symbol fetch ─────────────────────────────────────────────────────

with st.expander("Fetch a single symbol"):
    single_sym = st.text_input("Symbol", placeholder="e.g. AAPL").strip().upper()
    if single_sym and st.button("Fetch"):
        with st.spinner(f"Fetching {single_sym}..."):
            prices, errs = fetch_daily_prices(single_sym, start=start_date, end=end_date)
        if errs:
            for err in errs:
                st.warning(err)
        if prices:
            rows = [
                {
                    "symbol": p.symbol,
                    "price_date": str(p.price_date),
                    "open": float(p.open),
                    "high": float(p.high),
                    "low": float(p.low),
                    "close": float(p.close),
                    "adj_close": float(p.adj_close),
                    "volume": p.volume,
                }
                for p in prices
            ]
            price_data = st.session_state.get("price_data", {})
            price_data[single_sym] = pd.DataFrame(rows)
            st.session_state["price_data"] = price_data
            st.success(f"{single_sym}: {len(rows)} price rows fetched.")
        else:
            st.warning(f"No data returned for {single_sym}.")

st.divider()

# ── View stored prices ──────────────────────────────────────────────────────

st.subheader("Price Data")

price_data: dict[str, pd.DataFrame] = st.session_state.get("price_data", {})

if not price_data:
    st.info("No prices fetched yet. Use the fetch button above.")
    st.stop()

# Latest price summary
latest_data = []
for sym in symbols_with_benchmarks:
    df = price_data.get(sym)
    if df is not None and not df.empty:
        last_row = df.sort_values("price_date").iloc[-1]
        latest_data.append({
            "Symbol": sym,
            "Date": last_row["price_date"],
            "Close": last_row["close"],
            "Adj Close": last_row["adj_close"],
            "Volume": int(last_row["volume"]),
        })

if latest_data:
    latest_df = pd.DataFrame(latest_data)
    st.dataframe(
        latest_df,
        use_container_width=True,
        column_config={
            "Close": st.column_config.NumberColumn("Close", format="$%.2f"),
            "Adj Close": st.column_config.NumberColumn("Adj Close", format="$%.2f"),
            "Volume": st.column_config.NumberColumn("Volume", format="%d"),
        },
    )

# Per-symbol chart
available_syms = [s for s in symbols_with_benchmarks if s in price_data]
selected_sym = st.selectbox("View price chart", [""] + available_syms)

if selected_sym:
    df = price_data[selected_sym].copy()
    df["price_date"] = pd.to_datetime(df["price_date"])
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce")
    df = df.sort_values("price_date")

    fig = go.Figure(
        go.Scatter(
            x=df["price_date"],
            y=df["adj_close"],
            mode="lines",
            line=dict(color=COLORS["primary"], width=2),
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>$%{y:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=400,
        yaxis_tickformat="$,.2f",
        xaxis_title=None,
        yaxis_title="Adj Close",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Raw data"):
        st.dataframe(df, use_container_width=True)
