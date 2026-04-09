"""Stock Metrics — fundamental data from SEC EDGAR."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.fetcher import fetch_metrics_for_symbol
from src.splits import (
    SPLIT_AFFECTED_PER_SHARE,
    SPLIT_AFFECTED_SHARE_COUNT,
    detect_splits,
    normalize_latest_value,
    normalize_metrics,
)
from src.ttm import compute_quarterly_latest, compute_ttm, compute_ttm_latest, is_flow_metric
from src.ui_helpers import COLORS, inject_metric_card_css

st.title("Stock Metrics")
inject_metric_card_css()

# ── Guard: need symbols ─────────────────────────────────────────────────────

symbols = st.session_state.get("symbols", [])
if not symbols:
    st.info("No symbols configured. Go to the main page and enter ticker symbols.")
    st.stop()

st.caption(f"{len(symbols)} symbols configured")

# ── Fetch controls ──────────────────────────────────────────────────────────

metrics_data: dict = st.session_state.get("metrics_data", {})
has_data = bool(metrics_data)

with st.expander("Fetch from SEC EDGAR", expanded=not has_data):
    fetch_symbols = st.multiselect(
        "Symbols to fetch",
        options=symbols,
        default=symbols,
        help="Select which symbols to fetch fundamental data for. ETFs typically have no SEC filings.",
    )

    col_fetch, col_status = st.columns([1, 3])
    with col_fetch:
        fetch_clicked = st.button("Fetch Metrics", type="primary", disabled=not fetch_symbols)

    if fetch_clicked:
        all_metrics = []
        all_errors = []
        progress = st.progress(0, text="Starting...")

        for i, sym in enumerate(fetch_symbols):
            progress.progress(
                (i + 1) / len(fetch_symbols),
                text=f"Fetching {sym} ({i + 1}/{len(fetch_symbols)})...",
            )
            fetched, errors = fetch_metrics_for_symbol(sym)
            all_metrics.extend(fetched)
            all_errors.extend(errors)

        progress.empty()

        if all_metrics:
            # Organize metrics into {symbol: {metric_name: {latest row data}}}
            organized: dict[str, dict[str, dict]] = {}
            # Also store raw history per symbol+metric for detail views
            raw_history: dict[str, list[dict]] = st.session_state.get("metrics_raw_history", {})

            for m in all_metrics:
                sym = m.symbol
                name = m.metric_name
                row = {
                    "metric_value": float(m.metric_value),
                    "period_end": str(m.period_end),
                    "period_start": str(m.period_start) if m.period_start else None,
                    "fiscal_period": m.fiscal_period,
                    "fiscal_year": m.fiscal_year,
                    "filing_type": m.filing_type,
                    "duration_days": m.duration_days,
                    "reporting_style": m.reporting_style,
                    "cik": m.cik,
                    "source": m.source,
                }

                # Accumulate history
                history_key = f"{sym}:{name}"
                raw_history.setdefault(history_key, []).append(row)

                # Keep latest by period_end
                if sym not in organized:
                    organized[sym] = {}
                existing = organized[sym].get(name)
                if existing is None or str(row["period_end"]) > str(existing["period_end"]):
                    organized[sym][name] = row

            st.session_state["metrics_data"] = organized
            st.session_state["metrics_raw_history"] = raw_history
            metrics_data = organized

            st.success(
                f"Done: {len(all_metrics)} metrics fetched across {len(fetch_symbols)} symbols."
            )
        else:
            st.warning("No metrics were fetched. Check the issues below.")

        if all_errors:
            with st.expander(f"Issues ({len(all_errors)})", expanded=len(all_metrics) == 0):
                for err in all_errors:
                    st.text(err)

# ── Guard: need data to continue ────────────────────────────────────────────

if not metrics_data:
    st.info("No metrics fetched yet. Use the **Fetch Metrics** section above.")
    st.stop()

# ── Pre-compute split detection per symbol ──────────────────────────────────

_splits_cache: dict[str, list] = {}
raw_history = st.session_state.get("metrics_raw_history", {})


def _get_history(sym: str, metric_name: str) -> list[dict]:
    return raw_history.get(f"{sym}:{metric_name}", [])


def _get_splits_for_symbol(sym: str) -> list:
    if sym not in _splits_cache:
        shares = _get_history(sym, "shares_outstanding")
        eps = _get_history(sym, "eps_diluted")
        _splits_cache[sym] = detect_splits(shares, eps)
    return _splits_cache[sym]


# ── Build summary data ──────────────────────────────────────────────────────

DISPLAY_METRICS = [
    ("revenue", "Revenue", "$%.0f"),
    ("net_income", "Net Income", "$%.0f"),
    ("eps_diluted", "EPS (Diluted)", "$%.2f"),
    ("total_assets", "Total Assets", "$%.0f"),
    ("total_liabilities", "Total Liabilities", "$%.0f"),
    ("stockholders_equity", "Equity", "$%.0f"),
    ("shares_outstanding", "Shares Out", "%.0f"),
    ("operating_income", "Operating Income", "$%.0f"),
    ("cash_and_equivalents", "Cash", "$%.0f"),
]

rows = []
split_notes: list[str] = []
ttm_notes: list[str] = []

for sym in sorted(metrics_data.keys()):
    sym_metrics = metrics_data[sym]
    sym_splits = _get_splits_for_symbol(sym)
    row: dict = {"Symbol": sym}

    for metric_key, display_name, fmt in DISPLAY_METRICS:
        if metric_key in sym_metrics:
            raw_val = sym_metrics[metric_key].get("metric_value")
            fp = sym_metrics[metric_key].get("fiscal_period", "")
            period = sym_metrics[metric_key].get("period_end", "")
            if raw_val is not None:
                try:
                    val = float(raw_val)

                    if is_flow_metric(metric_key) and fp:
                        history = _get_history(sym, metric_key)
                        history = normalize_metrics(history, sym_splits, metric_key)
                        vk = "normalized_value" if sym_splits else "metric_value"

                        try:
                            q_val, q_label, q_method = compute_quarterly_latest(history, value_key=vk)
                        except TypeError:
                            q_val, q_label, q_method = None, None, None

                        try:
                            ttm_val, ttm_method = compute_ttm_latest(history, value_key=vk)
                        except TypeError:
                            ttm_val, ttm_method = None, None

                        if q_val is not None:
                            val = q_val
                            if sym not in [n.split(":")[0] for n in ttm_notes]:
                                ttm_notes.append(f"{sym}: {q_label} quarterly from {fp} data")
                        elif ttm_val is not None:
                            val = ttm_val
                            if sym not in [n.split(":")[0] for n in ttm_notes]:
                                ttm_notes.append(f"{sym}: TTM (annual) from {fp} data")

                        if sym_splits and sym not in [n.split(":")[0] for n in split_notes]:
                            split_notes.append(f"{sym}: {len(sym_splits)} split(s) detected")
                    elif sym_splits and metric_key in (SPLIT_AFFECTED_PER_SHARE | SPLIT_AFFECTED_SHARE_COUNT):
                        val, was_adjusted = normalize_latest_value(
                            metric_key, val, str(period), sym_splits,
                        )
                        if was_adjusted and sym not in [n.split(":")[0] for n in split_notes]:
                            split_notes.append(f"{sym}: {len(sym_splits)} split(s) detected")

                    row[display_name] = val
                except (ValueError, TypeError):
                    row[display_name] = None
            else:
                row[display_name] = None
        else:
            row[display_name] = None

    any_metric = next(iter(sym_metrics.values()), {})
    row["Period End"] = any_metric.get("period_end", "—")
    row["Filing"] = any_metric.get("filing_type", "—")
    row["FP"] = any_metric.get("fiscal_period", "—")
    rows.append(row)


# ── Main content: three tabs ────────────────────────────────────────────────

tab_overview, tab_heatmap, tab_detail = st.tabs(
    ["Portfolio Fundamentals", "Comparison Heatmap", "Symbol Detail"]
)

# ═══ TAB 1: Portfolio Fundamentals ══════════════════════════════════════════

with tab_overview:
    if rows:
        summary_df = pd.DataFrame(rows)

        total_revenue = summary_df["Revenue"].sum()
        total_net_income = summary_df["Net Income"].sum()
        total_equity = summary_df["Equity"].sum()
        total_cash = summary_df["Cash"].sum()
        symbols_count = len(summary_df)

        c1, c2, c3, c4, c5 = st.columns(5, gap="medium")
        c1.metric("Symbols", symbols_count)
        c2.metric("Total Revenue", f"${total_revenue:,.0f}")
        c3.metric(
            "Total Net Income",
            f"${total_net_income:,.0f}",
            delta=f"{'Positive' if total_net_income >= 0 else 'Negative'}",
            delta_color="normal" if total_net_income >= 0 else "inverse",
        )
        c4.metric("Total Equity", f"${total_equity:,.0f}")
        c5.metric("Total Cash", f"${total_cash:,.0f}")

        st.markdown("---")

        def _color_pnl(val):
            if pd.isna(val):
                return ""
            try:
                v = float(val)
            except (ValueError, TypeError):
                return ""
            if v > 0:
                return "color: #22c55e; font-weight: 600"
            elif v < 0:
                return "color: #ef4444; font-weight: 600"
            return ""

        colored_cols = ["Net Income", "EPS (Diluted)", "Operating Income"]
        metrics_col_config = {
            display_name: st.column_config.NumberColumn(display_name, format=fmt)
            for _, display_name, fmt in DISPLAY_METRICS
        }

        _style_subset = [c for c in colored_cols if c in summary_df.columns]
        styled = summary_df.style.map(_color_pnl, subset=_style_subset)
        st.dataframe(
            styled,
            use_container_width=True,
            hide_index=True,
            column_config=metrics_col_config,
            height=min(400, 38 + len(summary_df) * 35),
        )

        notes = []
        if ttm_notes:
            notes.append("TTM-adjusted: " + " | ".join(ttm_notes))
        if split_notes:
            notes.append("Split-adjusted: " + " | ".join(split_notes))
        if notes:
            st.caption(" · ".join(notes))
    else:
        st.info("No metrics to display.")


# ═══ TAB 2: Comparison Heatmap ══════════════════════════════════════════════

with tab_heatmap:
    if rows:
        heatmap_df = pd.DataFrame(rows).set_index("Symbol")

        heatmap_metrics = ["Revenue", "Net Income", "EPS (Diluted)", "Equity",
                           "Operating Income", "Cash"]
        available_hm = [c for c in heatmap_metrics if c in heatmap_df.columns]

        if len(available_hm) >= 2 and len(heatmap_df) >= 2:
            st.subheader("Fundamentals Comparison")
            st.caption(
                "Each cell shows a z-score: how many standard deviations above/below "
                "the average. Green = above average, red = below."
            )

            hm_data = heatmap_df[available_hm].apply(pd.to_numeric, errors="coerce")

            means = hm_data.mean()
            stds = hm_data.std().replace(0, 1)
            z_scores = (hm_data - means) / stds

            fig = go.Figure(
                data=go.Heatmap(
                    z=z_scores.values,
                    x=z_scores.columns.tolist(),
                    y=z_scores.index.tolist(),
                    colorscale=[
                        [0, "#ef4444"],
                        [0.5, "#f8fafc"],
                        [1, "#22c55e"],
                    ],
                    zmid=0,
                    text=hm_data.map(
                        lambda v: f"${v:,.0f}" if pd.notna(v) else "—"
                    ).values,
                    texttemplate="%{text}",
                    textfont=dict(size=11),
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "%{x}: %{text}<br>"
                        "Z-score: %{z:.2f}"
                        "<extra></extra>"
                    ),
                    showscale=True,
                    colorbar=dict(title="Z-Score", thickness=12, len=0.6),
                )
            )
            fig.update_layout(
                height=max(300, len(heatmap_df) * 45 + 80),
                xaxis=dict(side="top", tickangle=0),
                yaxis=dict(autorange="reversed"),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("---")
            st.subheader("Compare Metric Across Symbols")
            compare_metric = st.selectbox("Select metric", options=available_hm, index=0, key="compare_metric")

            if compare_metric:
                bar_data = hm_data[[compare_metric]].dropna().sort_values(compare_metric, ascending=True)
                colors = [
                    COLORS["profit"] if v >= 0 else COLORS["loss"]
                    for v in bar_data[compare_metric]
                ]
                fig_bar = go.Figure(
                    go.Bar(
                        x=bar_data[compare_metric],
                        y=bar_data.index,
                        orientation="h",
                        marker_color=colors,
                        hovertemplate="<b>%{y}</b><br>%{x:$,.0f}<extra></extra>",
                    )
                )
                fig_bar.update_layout(
                    height=max(250, len(bar_data) * 35 + 60),
                    xaxis_tickformat="$,.0f",
                    yaxis=dict(autorange="reversed"),
                    margin=dict(l=0, r=0, t=10, b=0),
                )
                st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("Need at least 2 symbols with data to show comparison heatmap.")
    else:
        st.info("No metrics data available for comparison.")


# ═══ TAB 3: Symbol Detail ══════════════════════════════════════════════════

with tab_detail:
    symbols_with_data = sorted(metrics_data.keys())
    if not symbols_with_data:
        st.stop()

    detail_symbol = st.selectbox("Select symbol", options=symbols_with_data, key="detail_symbol")

    if detail_symbol:
        latest = metrics_data.get(detail_symbol, {})
        if not latest:
            st.info(f"No metrics for {detail_symbol}.")
        else:
            detected_splits = _get_splits_for_symbol(detail_symbol)

            card_metrics = [
                ("revenue", "Revenue"),
                ("net_income", "Net Income"),
                ("eps_diluted", "EPS (Diluted)"),
                ("operating_income", "Operating Income"),
            ]

            cols = st.columns(len(card_metrics), gap="medium")
            for col, (key, label) in zip(cols, card_metrics):
                if key in latest:
                    raw = latest[key].get("metric_value")
                    fp = latest[key].get("fiscal_period", "")
                    try:
                        val = float(raw)
                        suffixes = []

                        if is_flow_metric(key) and fp:
                            history = _get_history(detail_symbol, key)
                            history = normalize_metrics(history, detected_splits, key)
                            vk = "normalized_value" if detected_splits else "metric_value"

                            try:
                                q_val, q_label, q_method = compute_quarterly_latest(history, value_key=vk)
                            except TypeError:
                                q_val, q_label, q_method = None, None, None

                            try:
                                ttm_val, ttm_method = compute_ttm_latest(history, value_key=vk)
                            except TypeError:
                                ttm_val, ttm_method = None, None

                            if q_val is not None:
                                val = q_val
                                suffixes.append(f"{q_label}")
                            elif ttm_val is not None:
                                val = ttm_val
                                suffixes.append("TTM")

                            if detected_splits:
                                suffixes.append("adj")
                        elif detected_splits and key in (SPLIT_AFFECTED_PER_SHARE | SPLIT_AFFECTED_SHARE_COUNT):
                            period = latest[key].get("period_end", "")
                            val, was_adj = normalize_latest_value(key, val, str(period), detected_splits)
                            if was_adj:
                                suffixes.append("adj")

                        adjusted_label = label
                        if suffixes:
                            adjusted_label = f"{label} ({', '.join(suffixes)})"

                        display = f"${val:,.2f}" if key.startswith("eps") else f"${val:,.0f}"
                        delta_color = "normal" if val >= 0 else "inverse"
                        col.metric(
                            adjusted_label,
                            display,
                            delta="Positive" if val >= 0 else "Negative",
                            delta_color=delta_color,
                        )
                    except (ValueError, TypeError):
                        col.metric(label, str(raw))
                else:
                    col.metric(label, "—")

            if detected_splits:
                with st.expander(f"Detected splits ({len(detected_splits)})", expanded=True):
                    for sp in detected_splits:
                        st.markdown(
                            f"**{sp.period_end}** — ratio {sp.shares_ratio:.2f}x ({sp.confidence} confidence)"
                        )
                        st.caption(sp.reason)

            # Historical data
            st.markdown("---")
            st.subheader("Historical Metrics")

            hist_metric = st.selectbox("Metric", options=sorted(latest.keys()), key="hist_metric")

            if hist_metric:
                history = _get_history(detail_symbol, hist_metric)
                if history:
                    history.sort(key=lambda r: str(r.get("period_end", "")))
                    history = normalize_metrics(history, detected_splits, hist_metric)

                    has_ttm = False
                    ttm_value_key = "normalized_value" if detected_splits else "metric_value"
                    if is_flow_metric(hist_metric):
                        try:
                            ttm_history = compute_ttm(history, value_key=ttm_value_key)
                        except TypeError:
                            ttm_history = []
                        ttm_by_pe = {str(r.get("period_end", "")): r for r in ttm_history}
                        for orig in history:
                            pe = str(orig.get("period_end", ""))
                            ttm_row = ttm_by_pe.get(pe, {})
                            orig["ttm_value"] = ttm_row.get("ttm_value")
                        has_ttm = any(r.get("ttm_value") is not None for r in history)

                    if has_ttm:
                        value_col = "ttm_value"
                    elif detected_splits:
                        value_col = "normalized_value"
                    else:
                        value_col = "metric_value"

                    hist_df = pd.DataFrame(history)
                    hist_df["period_end"] = pd.to_datetime(hist_df["period_end"])
                    hist_df[value_col] = pd.to_numeric(hist_df[value_col], errors="coerce")
                    hist_df = hist_df.sort_values("period_end")

                    sub_chart, sub_table = st.tabs(["Trend Chart", "Data Table"])

                    with sub_chart:
                        chart_df = hist_df.dropna(subset=[value_col])
                        if not chart_df.empty:
                            y_vals = chart_df[value_col].tolist()
                            bar_colors = [
                                COLORS["profit"] if v >= 0 else COLORS["loss"]
                                for v in y_vals
                            ]

                            if len(chart_df) <= 20:
                                fig = go.Figure(
                                    go.Bar(
                                        x=chart_df["period_end"],
                                        y=chart_df[value_col],
                                        marker_color=bar_colors,
                                        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Value: %{y:$,.0f}<extra></extra>",
                                    )
                                )
                            else:
                                fig = go.Figure(
                                    go.Scatter(
                                        x=chart_df["period_end"],
                                        y=chart_df[value_col],
                                        mode="lines+markers",
                                        line=dict(color=COLORS["primary"], width=2),
                                        marker=dict(size=5),
                                        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Value: %{y:$,.0f}<extra></extra>",
                                    )
                                )

                            fig.add_hline(y=0, line_dash="dot", line_color="#94a3b8", line_width=1)
                            fig.update_layout(
                                height=400,
                                yaxis_tickformat="$,.0f",
                                xaxis_title=None,
                                yaxis_title=hist_metric.replace("_", " ").title(),
                                showlegend=False,
                            )
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.info("No numeric data to chart.")

                    with sub_table:
                        display_cols = ["period_end", "fiscal_period", "fiscal_year",
                                        "filing_type", "metric_value", "duration_days",
                                        "reporting_style"]
                        if has_ttm:
                            display_cols.append("ttm_value")
                        if detected_splits:
                            display_cols.extend(["normalized_value", "split_adjusted"])
                        available = [c for c in display_cols if c in hist_df.columns]
                        st.dataframe(hist_df[available].reset_index(drop=True), use_container_width=True, hide_index=True)
                else:
                    st.info(f"No historical data for {hist_metric}.")
