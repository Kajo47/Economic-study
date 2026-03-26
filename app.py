# app.py
# Petroleum Economics Dashboard - Final Corrected Version
# Supports DD/MM/YYYY dates in Historic mode

import streamlit as st
import pandas as pd
import numpy as np
import numpy_financial as npf
from datetime import date
from dateutil.relativedelta import relativedelta
import plotly.express as px
import plotly.graph_objects as go
import io
from io import StringIO

st.set_page_config(page_title="Petroleum Economics Dashboard", layout="wide")

# ───────────────────────────────────────────────
# Session state
# ───────────────────────────────────────────────
if 'wells' not in st.session_state:
    st.session_state.wells = []
if 'project_capex' not in st.session_state:
    st.session_state.project_capex = []
if 'maintenance_items' not in st.session_state:
    st.session_state.maintenance_items = []
if 'run_clicked' not in st.session_state:
    st.session_state.run_clicked = False

# ───────────────────────────────────────────────
# Sidebar Inputs
# ───────────────────────────────────────────────
st.sidebar.title("Project Inputs")

# Mode selector
mode = st.sidebar.radio(
    "Analysis Mode",
    options=["Forecast (decline curve)", "Historic / Actual production"],
    index=0,
    key="analysis_mode"
)

# Time frame
col1, col2 = st.sidebar.columns(2)
start_date = col1.date_input("Study Start Date", date(2025, 6, 1), key="global_start")
end_date   = col2.date_input("Study End Date",   date(2030, 6, 1), key="global_end")

# Economic Parameters
st.sidebar.subheader("Economic Parameters")
annual_discount_pct = st.sidebar.slider("Annual Discount Rate (%)", 0.0, 30.0, 10.0, step=0.5, key="discount_rate") / 100

if mode == "Forecast (decline curve)":
    annual_decline_pct = st.sidebar.slider("Annual Decline Rate (%)", 0.0, 50.0, 10.0, step=0.5, key="decline_rate") / 100

# Oil prices
oil_prices = st.sidebar.multiselect(
    "Oil Price Scenarios (USD/bbl)",
    options=[40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 150, 200],
    default=[60, 70, 80],
    key="oil_prices"
)
custom_price = st.sidebar.number_input("Add custom oil price", min_value=0.0, value=0.0, step=1.0, key="custom_price")
if custom_price > 0 and custom_price not in oil_prices:
    oil_prices.append(custom_price)

# ── Add Wells ─────────────────────────────────────
st.sidebar.subheader("Add Wells")

with st.sidebar.expander("New Well", expanded=True):
    well_name    = st.text_input("Well Name / ID", "", key=f"well_name_{len(st.session_state.wells)}")
    well_capex   = st.number_input("CapEx (MM USD)", 0.0, 100.0, 4.0, step=0.1, key=f"capex_{len(st.session_state.wells)}")
    drill_date   = st.date_input("First Production Date", start_date, key=f"drill_{len(st.session_state.wells)}")
    opex_per_bbl = st.number_input("OpEx per bbl (USD)", 0.0, 100.0, 10.0, step=0.5, key=f"opex_{len(st.session_state.wells)}")

    if mode == "Forecast (decline curve)":
        initial_rate = st.number_input("Initial Oil Rate (bbl/day)", 0.0, 5000.0, 400.0, step=10.0, key=f"rate_{len(st.session_state.wells)}")
    else:
        st.markdown("**Upload CSV or paste data** (Date in DD/MM/YYYY)")
        uploaded_csv = st.file_uploader("Upload CSV", type=["csv"], key=f"upload_{len(st.session_state.wells)}")
        paste_data = st.text_area("Or paste here (Date,Oil_bbl)", height=140, key=f"paste_{len(st.session_state.wells)}")

    if st.button("Add Well", key=f"add_well_{len(st.session_state.wells)}") and well_name.strip():
        well_data = {
            "name": well_name.strip(),
            "capex_mm": well_capex,
            "drill_date": drill_date,
            "opex_per_bbl": opex_per_bbl,
            "mode": "forecast" if mode == "Forecast (decline curve)" else "historic"
        }

        if mode == "Forecast (decline curve)":
            well_data["initial_rate"] = initial_rate
            st.session_state.wells.append(well_data)
            st.success(f"Added forecast well: {well_name}")
        else:
            # Historic mode - robust parsing with dayfirst=True
            actual_df = None
            try:
                if uploaded_csv is not None:
                    actual_df = pd.read_csv(uploaded_csv)
                elif paste_data.strip():
                    actual_df = pd.read_csv(StringIO(paste_data))

                if actual_df is not None:
                    actual_df.columns = actual_df.columns.str.lower().str.strip()

                    date_col = next((c for c in actual_df.columns if any(x in c for x in ['date', 'month', 'period'])), None)
                    prod_col = next((c for c in actual_df.columns if any(x in c for x in ['oil', 'prod', 'bbl', 'production'])), None)

                    if date_col and prod_col:
                        actual_df = actual_df[[date_col, prod_col]].rename(columns={date_col: 'date', prod_col: 'oil_bbl'})
                        actual_df = actual_df.dropna()
                        # Force DD/MM/YYYY parsing
                        actual_df['date'] = pd.to_datetime(actual_df['date'], dayfirst=True, errors='coerce').dt.date
                        actual_df = actual_df.dropna(subset=['date'])

                        well_data["actual_df"] = actual_df
                        st.session_state.wells.append(well_data)
                        st.success(f"Added historic well: {well_name} ({len(actual_df)} months loaded)")
                    else:
                        st.error("Could not detect date and production columns.")
            except Exception as e:
                st.error(f"Failed to parse data: {str(e)} — well not added.")

# Wells list + delete
if st.session_state.wells:
    st.sidebar.subheader("Wells Added")
    for idx, well in enumerate(st.session_state.wells):
        col1, col2 = st.sidebar.columns([5, 1])
        mode_str = "Historic" if well.get("mode") == "historic" else "Forecast"
        extra = f" | {len(well.get('actual_df', []))} months" if well.get("mode") == "historic" else ""
        label = f"{well['name']} | {mode_str}{extra} | ${well['capex_mm']:.1f} MM | {well['drill_date']}"
        col1.markdown(label)
        if col2.button("🗑", key=f"del_well_{idx}"):
            st.session_state.wells.pop(idx)
            st.rerun()

# ── Project Capex ─────────────────────────────────
st.sidebar.subheader("Project Capex Items")

with st.sidebar.expander("Add Project Capex"):
    proj_desc = st.text_input("Description", "", key="proj_desc")
    proj_amt  = st.number_input("Amount (MM USD)", 0.0, 1000.0, 10.0, step=1.0, key="proj_amt")
    proj_date = st.date_input("Spend Date", start_date, key="proj_date")

    if st.button("Add Project Capex", key="add_proj") and proj_desc.strip():
        st.session_state.project_capex.append({
            "description": proj_desc.strip(),
            "capex_mm": proj_amt,
            "date": proj_date
        })
        st.success(f"Added: {proj_desc}")

if st.session_state.project_capex:
    st.sidebar.subheader("Project Capex Added")
    for idx, item in enumerate(st.session_state.project_capex):
        col1, col2 = st.sidebar.columns([5, 1])
        label = f"{item['description']} | ${item['capex_mm']:.1f} MM | {item['date']}"
        col1.markdown(label)
        if col2.button("🗑", key=f"del_proj_{idx}"):
            st.session_state.project_capex.pop(idx)
            st.rerun()

# ── Maintenance ───────────────────────────────────
st.sidebar.subheader("Maintenance Costs")

with st.sidebar.expander("Add Maintenance Item"):
    maint_desc = st.text_input("Description", "", key="maint_desc")
    maint_amt  = st.number_input("Amount (MM USD)", 0.0, 50.0, 1.0, step=0.5, key="maint_amt")
    maint_date = st.date_input("Spend Date", start_date, key="maint_date")

    if st.button("Add Maintenance", key="add_maint") and maint_desc.strip():
        st.session_state.maintenance_items.append({
            "description": maint_desc.strip(),
            "amount_mm": maint_amt,
            "date": maint_date
        })
        st.success(f"Added: {maint_desc}")

if st.session_state.maintenance_items:
    st.sidebar.subheader("Maintenance Added")
    for idx, item in enumerate(st.session_state.maintenance_items):
        col1, col2 = st.sidebar.columns([5, 1])
        label = f"{item['description']} | ${item['amount_mm']:.1f} MM | {item['date']}"
        col1.markdown(label)
        if col2.button("🗑", key=f"del_maint_{idx}"):
            st.session_state.maintenance_items.pop(idx)
            st.rerun()

# ───────────────────────────────────────────────
# Main Calculation & Dashboard
# ───────────────────────────────────────────────

st.title("Petroleum Project Economics Dashboard")

if not st.session_state.wells:
    st.info("Add at least one well to run the model.")
else:
    if st.button("RUN ECONOMIC MODEL", type="primary", use_container_width=True, key="run_model"):
        st.session_state.run_clicked = True

    if st.session_state.run_clicked:
        with st.spinner("Calculating cash flows..."):
            # Create monthly timeline
            months = []
            current = date(start_date.year, start_date.month, 1)
            end = date(end_date.year, end_date.month, end_date.day)

            while current <= end:
                next_start = date(current.year + (current.month == 12), ((current.month % 12) + 1), 1)
                days = (next_start - current).days
                months.append({"date": current, "days": days})
                current = next_start

            df = pd.DataFrame(months)
            df["gross_production_bbl"] = 0.0
            df["opex_mm"] = 0.0
            df["capex_mm"] = 0.0

            monthly_decline_factor = 1 - annual_decline_pct / 12 if mode == "Forecast (decline curve)" else None
            monthly_discount_rate = annual_discount_pct / 12

            # Process each well
            for well in st.session_state.wells:
                drill_dt = date(well["drill_date"].year, well["drill_date"].month, 1)
                if drill_dt > df["date"].max():
                    continue

                idx_start = df[df["date"] >= drill_dt].index.min()
                if pd.isna(idx_start):
                    continue

                df.loc[idx_start, "capex_mm"] += well["capex_mm"]

                if well["mode"] == "historic" and "actual_df" in well:
                    # Historic mode - use actual data
                    for i, row in df.iterrows():
                        match = well["actual_df"][well["actual_df"]["date"] == row["date"]]
                        if not match.empty:
                            monthly_bbl = match["oil_bbl"].iloc[0]
                            df.loc[i, "gross_production_bbl"] += monthly_bbl
                            df.loc[i, "opex_mm"] += (monthly_bbl * well["opex_per_bbl"]) / 1_000_000
                else:
                    # Forecast mode
                    current_rate = well.get("initial_rate", 0)
                    for i in range(idx_start, len(df)):
                        monthly_bbl = current_rate * df.loc[i, "days"]
                        df.loc[i, "gross_production_bbl"] += monthly_bbl
                        df.loc[i, "opex_mm"] += (monthly_bbl * well["opex_per_bbl"]) / 1_000_000
                        if i < len(df) - 1:
                            current_rate *= monthly_decline_factor

            # Project Capex
            for item in st.session_state.project_capex:
                spend_dt = date(item["date"].year, item["date"].month, 1)
                idx = df[df["date"] == spend_dt].index
                if not idx.empty:
                    df.loc[idx, "capex_mm"] += item["capex_mm"]

            # Maintenance
            for item in st.session_state.maintenance_items:
                spend_dt = date(item["date"].year, item["date"].month, 1)
                idx = df[df["date"] == spend_dt].index
                if not idx.empty:
                    df.loc[idx, "capex_mm"] += item["amount_mm"]

            df["total_cost_mm"] = df["capex_mm"] + df["opex_mm"]

            # Scenarios
            results = {}
            cum_prod = df["gross_production_bbl"].sum() / 1_000_000

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
                    irr_m = npf.irr(cash_flows)
                    irr_a = (1 + irr_m) ** 12 - 1 if not np.isnan(irr_m) else 0.0
                except:
                    irr_a = 0.0

                payback_idx = df[df[col_cum] >= 0].index.min()
                payback = "> study period" if pd.isna(payback_idx) else f"{(payback_idx + 1)/12:.1f} years"

                undisc_cf = df[col_cf].sum()
                tot_out = df["total_cost_mm"].sum()
                roi = undisc_cf / tot_out if tot_out > 0 else 0

                results[price] = {
                    "NPV_MM": npv,
                    "IRR_pct": irr_a * 100,
                    "ROI": roi,
                    "Payback": payback,
                    "CumProduction_MMbbl": cum_prod
                }

        # Dashboard
        st.subheader("Key Economic Indicators")
        cols = st.columns(len(results))
        for i, (price, r) in enumerate(sorted(results.items())):
            with cols[i]:
                st.metric(f"${price}/bbl", f"NPV ${r['NPV_MM']:,.1f} MM", delta=f"IRR {r['IRR_pct']:.1f}%")
                st.caption(f"Payback: {r['Payback']}")
                st.caption(f"ROI: {r['ROI']:.2f}")

        # Charts
        tab1, tab2, tab3 = st.tabs(["Production", "Monthly Cash Flow", "Cumulative Cash Flow"])

        with tab1:
            fig_prod = px.line(df, x="date", y="gross_production_bbl", title="Monthly Production (bbl)")
            st.plotly_chart(fig_prod, use_container_width=True)

        with tab2:
            fig_cf = go.Figure()
            for p in sorted(results.keys()):
                fig_cf.add_trace(go.Scatter(x=df["date"], y=df[f"cashflow_mm_{p}"], name=f"${p}", mode="lines"))
            fig_cf.update_layout(title="Monthly Cash Flow (MM USD)")
            st.plotly_chart(fig_cf, use_container_width=True)

        with tab3:
            fig_cum = go.Figure()
            for p in sorted(results.keys()):
                fig_cum.add_trace(go.Scatter(x=df["date"], y=df[f"cum_cf_{p}"], name=f"${p}", fill="tozeroy"))
            fig_cum.update_layout(title="Cumulative Cash Flow (MM USD)")
            st.plotly_chart(fig_cum, use_container_width=True)

        # Export
        st.subheader("Export Results")

        def create_excel_export():
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                workbook = writer.book
                header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9EAD3', 'border': 1})

                df.to_excel(writer, sheet_name='Cash Flow', index=False)
                ws = writer.sheets['Cash Flow']
                for c, val in enumerate(df.columns):
                    ws.write(0, c, val, header_fmt)
                ws.set_column(0, len(df.columns)-1, 14)

                summary_data = [{
                    'Oil Price (USD/bbl)': p,
                    'NPV (MM USD)': round(r['NPV_MM'], 2),
                    'IRR (%)': round(r['IRR_pct'], 2),
                    'ROI (multiple)': round(r['ROI'], 3),
                    'Payback (years)': r['Payback'],
                    'Cum. Prod (MMbbl)': round(r['CumProduction_MMbbl'], 3)
                } for p, r in sorted(results.items())]
                pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary Metrics', index=False)

            output.seek(0)
            return output.getvalue()

        st.download_button(
            "Download Cash Flow + Summary (Excel)",
            create_excel_export(),
            f"Petroleum_Economics_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_excel"
        )

st.sidebar.markdown("---")
st.sidebar.caption("Forecast / Historic mode • Youssif.Sebak")
