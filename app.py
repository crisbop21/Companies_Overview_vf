import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from src.logging_config import setup_logging

setup_logging()

st.set_page_config(
    page_title="Companies Overview",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {
        padding-left: 2rem;
        padding-right: 2rem;
        padding-top: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Companies Overview")
st.markdown("Enter ticker symbols to fetch SEC fundamentals, Yahoo Finance prices, and view visualizations.")

# ── Symbol input ────────────────────────────────────────────────────────────

default_symbols = ", ".join(st.session_state.get("symbols", []))
raw = st.text_input(
    "Ticker symbols (comma-separated)",
    value=default_symbols,
    placeholder="e.g. AAPL, MSFT, GOOGL, AMZN, NVDA",
)

if st.button("Set Symbols", type="primary"):
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if symbols:
        st.session_state["symbols"] = symbols
        # Clear stale data when symbols change
        for key in ("metrics_data", "price_data"):
            st.session_state.pop(key, None)
        st.success(f"Tracking {len(symbols)} symbols: {', '.join(symbols)}")
    else:
        st.warning("Enter at least one ticker symbol.")

symbols = st.session_state.get("symbols", [])
if symbols:
    st.caption(f"Current symbols: **{', '.join(symbols)}**")

    has_metrics = bool(st.session_state.get("metrics_data"))
    has_prices = bool(st.session_state.get("price_data"))

    st.markdown("---")
    c1, c2 = st.columns(2)
    c1.metric("SEC Metrics", f"{len(st.session_state.get('metrics_data', {}))} symbols" if has_metrics else "Not fetched")
    c2.metric("Price Data", f"{len(st.session_state.get('price_data', {}))} symbols" if has_prices else "Not fetched")

    st.info("Use the pages in the sidebar to fetch data and view visualizations.")
