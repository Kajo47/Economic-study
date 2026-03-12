# app.py
# Petroleum Project Economics Dashboard - with Excel export (Option A)
# Run with: streamlit run app.py

import streamlit as st
import pandas as pd
import numpy as np
import numpy_financial as npf
from datetime import date
from dateutil.relativedelta import relativedelta
import plotly.express as px
import plotly.graph_objects as go
import io

st.set_page_config(page_title="Petroleum Economics Dashboard", layout="wide")

# ───────────────────────────────────────────────
# Session state initialization
# ───────────────────────────────────────────────

if 'wells' not in st.session_state:
    st.session_state.wells = []
if 'project_capex' not in st.session_state:
    st.session_state.project_capex = []
if 'run_clicked' not in st.session_state:
    st.session_state.run_clicked = False

# ───────────────────────────────────────────────
# Sidebar - Inputs
# ───────────────────────────────────────────────

st.sidebar.title("Project Inputs")

# ── Time frame ───────────────────────────────────
col1, col2 = st.sidebar.columns(2)
start_date = col1.date_input("Study Start Date", date(2025, 6, 1))
end_date   = col2.date_input("Study End Date",   date(2030, 6, 1))

# ── Global economic parameters ───────────────────
st.sidebar.subheader("Economic Parameters")
annual_decline_pct  = st.sidebar.slider("Annual Decline Rate (%)",  0.0, 50.0, 10.0, step=0.5) / 100
annual_discount_pct = st.sidebar.slider("Annual Discount Rate (%)", 0.0, 30.0, 10.0, step=0.5) / 100
opex_per_bbl        = st.sidebar.number_input("OpEx per bbl (USD)",  0.0, 100.0, 10.0, step=0.5)

# Oil price selection - no upper limit
oil_prices = st.sidebar.multiselect(
    "Oil Price Scenarios (USD/bbl)",
    options=[40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 150],
    default=[60, 70, 80]
)
custom_price = st.sidebar.number_input("Add custom oil price (USD/bbl)", min_value=0.0, value=0.0, step=1.0)
if custom_price > 0 and custom_price not in oil_prices:
    oil_prices.append(custom_price)

maintenance_annual_mm = st.sidebar.number_input(
    "Annual Maintenance Cost (MM USD)", 0.0, 50.0, 4.0, step=0.5
)

# ── Wells ────────────────────────────────────────
st.sidebar.subheader("Add Wells")

with st.sidebar.expander("New Well", expanded=True):
    well_name     = st.text_input("Well Name / ID", "")
    well_capex    = st.number_input("CapEx (MM USD)", 0.0, 100.0, 4.0, step=0.1)
    drill_date    = st.date_input("Drill / First Oil Date", start_date)
    initial_rate  = st.number_input("Initial Oil Rate (bbl/day)", 0.0, 5000.0, 400.0, step=10.0)

    if st.button("Add Well") and well_name.strip():
        st.session_state.wells.append({
            "name": well_name.strip(),
            "capex_mm": well_capex,
            "drill_date": drill_date,
            "initial_rate": initial_rate
        })
        st.success(f"Added: {well_name}")

# ── Wells management (compact style + delete) ─────
if st.session_state.wells:
    st.sidebar.subheader("Wells Added")

    for idx, well in enumerate(st.session_state.wells):
        col1, col2 = st.sidebar.columns([5, 1])
        label = f"{well['name']} | {well['initial_rate']:.0f} bbl/d | Drill: {well['drill_date']} | ${well['capex_mm']:.1f} MM"
        col1.markdown(label)
        if col2.button("🗑", key=f"del_well_{idx}", help="Remove this well"):
            st.session_state.wells.pop(idx)
            st.rerun()

    if st.sidebar.button("Clear All Wells"):
        st.session_state.wells = []
        st.rerun()

# ── Project Capex ─────────────────────────────────
st.sidebar.subheader("Project Capex Items")

with st.sidebar.expander("Add Project Capex"):
    item_desc  = st.text_input("Description", "")
    item_capex = st.number_input("Amount (MM USD)", 0.0, 1000.0, 10.0, step=1.0)
    item_date  = st.date_input("Spend Date", start_date)

    if st.button("Add Project Capex") and item_desc.strip():
        st.session_state.project_capex.append({
            "description": item_desc.strip(),
            "capex_mm": item_capex,
            "date": item_date
        })
        st.success(f"Added: {item_desc}")

if st.session_state.project_capex:
    st.sidebar.dataframe(
        pd.DataFrame(st.session_state.project_capex).style.format({"capex_mm": "${:,.1f}"}),
        use_container_width=True
    )

# ───────────────────────────────────────────────
# Main Area
# ───────────────────────────────────────────────

st.title("Petroleum Project Economics Dashboard")

if not st.session_state.wells:
    st.info("→ Add at least one well in the sidebar to run the model.")
else:
    if st.button("RUN ECONOMIC MODEL", type="primary", use_container_width=True):
        st.session_state.run_clicked = True

    if st.session_state.run_clicked:

        with st.spinner("Calculating monthly cash flows..."):

            # ── Monthly timeline ───────────────────────────────
            months = []
            current = date(start_date.year, start_date.month, 1)
            end = date(end_date.year, end_date.month, end_date.day)

            while current <= end:
                if current.month == 12:
                    next_month_start = date(current.year + 1, 1, 1)
                else:
                    next_month_start = date(current.year, current.month + 1, 1)

                days_in_month = (next_month_start - current).days

                months.append({
                    "date": current,
                    "days": days_in_month,
                    "month_idx": len(months)
                })

                current = next_month_start

            df = pd.DataFrame(months)

            monthly_decline_factor = 1 - annual_decline_pct / 12
            monthly_discount_rate = annual_discount_pct / 12

            df["gross_production_bbl"] = 0.0
            df["capex_mm"] = 0.0

            # ── Wells ──────────────────────────────────────────
            for well in st.session_state.wells:
                drill_dt = date(well["drill_date"].year, well["drill_date"].month, 1)
                if drill_dt < df["date"].min() or drill_dt > df["date"].max():
                    continue

                idx_start = df[df["date"] >= drill_dt].index.min()
                if pd.isna(idx_start):
                    continue

                df.loc[idx_start, "capex_mm"] += well["capex_mm"]

                current_rate = well["initial_rate"]
                for i in range(idx_start, len(df)):
                    monthly_bbl = current_rate * df.loc[i, "days"]
                    df.loc[i, "gross_production_bbl"] += monthly_bbl
                    if i < len(df) - 1:
                        current_rate *= monthly_decline_factor

            # ── Project Capex ──────────────────────────────────
            for item in st.session_state.project_capex:
                spend_dt = date(item["date"].year, item["date"].month, 1)
                idx = df[df["date"] == spend_dt].index
                if not idx.empty:
                    df.loc[idx, "capex_mm"] += item["capex_mm"]

            # ── Maintenance ────────────────────────────────────
            if maintenance_annual_mm > 0:
                monthly_maint = maintenance_annual_mm / 12
                df["capex_mm"] += monthly_maint

            df["opex_mm"] = df["gross_production_bbl"] * opex_per_bbl / 1_000_000
            df["total_cost_mm"] = df["capex_mm"] + df["opex_mm"]

            # ── Scenarios ──────────────────────────────────────
            results = {}
            cum_production = df["gross_production_bbl"].sum() / 1_000_000

            for price in sorted(set(oil_prices)):
                col_in = f"revenue_mm_{price}"
                col_cf = f"cashflow_mm_{price}"
                col_cum = f"cum_cf_{price}"

                df[col_in] = df["gross_production_bbl"] * price / 1_000_000
                df[col_cf] = df[col_in] - df["total_cost_mm"]
                df[col_cum] = df[col_cf].cumsum()

                cash_flows = df[col_cf].values

                npv = npf.npv(monthly_discount_rate, cash_flows)

                try:
                    irr_monthly = npf.irr(cash_flows)
                    irr_annual = (1 + irr_monthly) ** 12 - 1 if not np.isnan(irr_monthly) else 0.0
                except:
                    irr_annual = 0.0

                payback_idx = df[df[col_cum] >= 0].index.min()
                payback = "> study period" if pd.isna(payback_idx) else f"{(payback_idx + 1) / 12:.1f} years"

                undisc_cum_cf = df[col_cf].sum()
                total_cash_out = df["total_cost_mm"].sum()
                roi = undisc_cum_cf / total_cash_out if total_cash_out > 0 else 0

                results[price] = {
                    "NPV_MM": npv,
                    "IRR_pct": irr_annual * 100,
                    "ROI": roi,
                    "Payback": payback,
                    "CumProduction_MMbbl": cum_production
                }

        # ── Dashboard ──────────────────────────────────────

        st.subheader("Key Economic Indicators")

        cols = st.columns(len(results))
        for i, (price, r) in enumerate(sorted(results.items())):
            with cols[i]:
                st.metric(
                    f"${price}/bbl",
                    f"NPV ${r['NPV_MM']:,.1f} MM",
                    delta=f"IRR {r['IRR_pct']:.1f}%"
                )
                st.caption(f"Payback: {r['Payback']}")
                st.caption(f"ROI: {r['ROI']:.2f}")

        # ── Charts ─────────────────────────────────────────

        tab1, tab2, tab3 = st.tabs(["Production", "Monthly Cash Flow", "Cumulative Cash Flow"])

        with tab1:
            fig_prod = px.line(
                df, x="date", y="gross_production_bbl",
                title="Monthly Gross Production (bbl)"
            )
            st.plotly_chart(fig_prod, use_container_width=True)

        with tab2:
            fig_cf = go.Figure()
            for price in sorted(results.keys()):
                fig_cf.add_trace(go.Scatter(
                    x=df["date"], y=df[f"cashflow_mm_{price}"],
                    name=f"${price}",
                    mode="lines"
                ))
            fig_cf.update_layout(title="Monthly Cash Flow (MM USD)")
            st.plotly_chart(fig_cf, use_container_width=True)

        with tab3:
            fig_cum = go.Figure()
            for price in sorted(results.keys()):
                fig_cum.add_trace(go.Scatter(
                    x=df["date"], y=df[f"cum_cf_{price}"],
                    name=f"${price}",
                    fill="tozeroy"
                ))
            fig_cum.update_layout(title="Cumulative Cash Flow (MM USD)")
            st.plotly_chart(fig_cum, use_container_width=True)

        # ── Export to Excel (Option A) ──────────────────────────────────────

        st.subheader("Export Results")

        def create_excel_export():
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                workbook  = writer.book
                header_format = workbook.add_format({'bold': True, 'bg_color': '#D9EAD3', 'border': 1})

                # Sheet 1: Cash Flow
                df.to_excel(writer, sheet_name='Cash Flow', index=False)
                ws_cf = writer.sheets['Cash Flow']
                for col_num, value in enumerate(df.columns):
                    ws_cf.write(0, col_num, value, header_format)
                ws_cf.set_column(0, len(df.columns)-1, 14)

                # Sheet 2: Summary Metrics
                summary_rows = []
                for price, r in sorted(results.items()):
                    summary_rows.append({
                        'Oil Price (USD/bbl)': price,
                        'NPV (MM USD)': round(r['NPV_MM'], 2),
                        'IRR (%)': round(r['IRR_pct'], 2),
                        'ROI (multiple)': round(r['ROI'], 3),
                        'Payback (years)': r['Payback'],
                        'Cum. Production (MMbbl)': round(r['CumProduction_MMbbl'], 3)
                    })
                summary_df = pd.DataFrame(summary_rows)
                summary_df.to_excel(writer, sheet_name='Summary Metrics', index=False)
                ws_sum = writer.sheets['Summary Metrics']
                for col_num, value in enumerate(summary_df.columns):
                    ws_sum.write(0, col_num, value, header_format)
                ws_sum.set_column(0, len(summary_df.columns)-1, 18)

            output.seek(0)
            return output.getvalue()

        excel_bytes = create_excel_export()

        st.download_button(
            label="Download Cash Flow + Summary Metrics (Excel)",
            data=excel_bytes,
            file_name=f"Petroleum_Economics_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

st.sidebar.markdown("---")

st.sidebar.caption("Petroleum Economics Dashboard • Engineered by Youssif.Sebak")
