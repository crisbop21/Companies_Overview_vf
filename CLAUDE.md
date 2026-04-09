# Companies Overview

## What this is
A Streamlit app for comparing publicly traded companies using SEC EDGAR fundamentals and Yahoo Finance price data.

## Stack
- streamlit for UI
- pydantic for schema validation
- requests for SEC EDGAR API
- yfinance for Yahoo Finance prices
- pandas + numpy for data wrangling
- plotly for visualizations

## How to run locally
pip install -r requirements.txt
streamlit run app.py

## How to verify changes
streamlit run app.py and manually test the affected page.
Tests live in tests/ and run with `pytest`.

## Architecture
- Data is stored in `st.session_state` (no database)
- `src/fetcher.py` — SEC EDGAR data fetching
- `src/price_fetcher.py` — Yahoo Finance price fetching
- `src/technical.py` — 10 technical signals + composite scoring
- `src/valuation.py` — fundamental ratios, percentiles, composite scores
- `src/ttm.py` — trailing twelve months computation
- `src/splits.py` — stock split detection and normalization

## Constraints (non-negotiable)
- Free tiers only — no paid APIs
- No hardcoded credentials anywhere
- Financial figures must never be silently mutated
