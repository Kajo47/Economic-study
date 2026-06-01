import streamlit as st

# ======================== LOGIN GATE ========================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

def authenticate(username, password):
    return st.secrets.get("passwords", {}).get(username) == password

if not st.session_state.authenticated:
    with st.form("login"):
        st.title("Petroleum Economics Dashboard – Login")
        user = st.text_input("Username")
        pwd = st.text_input("Password", type="password")
        if st.form_submit_button("Login"):
            if authenticate(user, pwd):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Invalid credentials")
    st.stop()

with st.sidebar:
    if st.button("Logout"):
        st.session_state.authenticated = False
        st.rerun()
# ============================================================

import pandas as pd
import numpy as np
import numpy_financial as npf
from datetime import date
from io import BytesIO
import json
import zipfile
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

st.set_page_config(page_title="Petroleum Economics Dashboard", layout="wide")

# Session state init
for key, default in [
    ("wells", []),
    ("project_capex", []),
    ("maintenance_items", []),
    ("historic_data", {}),
    ("model_run", False),
    ("results", None),
    ("psa_royalty_pct", 0.1),
    ("psa_cost_recovery_ceiling", 0.4),
    ("psa_contractor_profit_share", 0.4),
]:
    if key not in st.session_state:
        st.session_state[key] = default

def monthly_decline_factor(annual_pct):
    annual_rate = annual_pct / 100.0
    return (1 - annual_rate) ** (1 / 12)

def serialise_inputs():
    if st.session_state.get("project_type", "PSA with Contractor") == "Government Self-Funded":
        royalty = 0.0; cost_rec = 1.0; profit_share = 1.0
    else:
        royalty = st.session_state.psa_royalty_pct
        cost_rec = st.session_state.psa_cost_recovery_ceiling
        profit_share = st.session_state.psa_contractor_profit_share
    return {
        "project_type": st.session_state.get("project_type", "PSA with Contractor"),
        "mode": st.session_state.get("mode_radio", "Forecast (decline curve)"),
        "start_date": st.session_state.start.strftime("%Y-%m-%d"),
        "end_date": st.session_state.end.strftime("%Y-%m-%d"),
        "royalty_pct": royalty,
        "cost_recovery_ceiling": cost_rec,
        "contractor_profit_share": profit_share,
        "annual_discount_pct": st.session_state.get("annual_discount_pct", 0.1) / 100.0,
        "oil_prices": st.session_state.get("oil_prices", [60,70,80]),
        "wells": st.session_state.wells,
        "project_capex": st.session_state.project_capex,
        "maintenance_items": st.session_state.maintenance_items,
    }

def load_inputs_callback():
    uploaded_file = st.session_state.load_json
    if uploaded_file is None: return
    try:
        data = json.load(uploaded_file)
        st.session_state.project_type = data.get("project_type", "PSA with Contractor")
        st.session_state.mode_radio = data.get("mode", "Forecast (decline curve)")
        st.session_state.start = date.fromisoformat(data["start_date"])
        st.session_state.end = date.fromisoformat(data["end_date"])
        st.session_state.psa_royalty_pct = data.get("royalty_pct", 0.1)
        st.session_state.psa_cost_recovery_ceiling = data.get("cost_recovery_ceiling", 0.4)
        st.session_state.psa_contractor_profit_share = data.get("contractor_profit_share", 0.4)
        st.session_state.annual_discount_pct = data.get("annual_discount_pct", 0.1) * 100.0
        st.session_state.oil_prices = data.get("oil_prices", [60,70,80])

        wells_loaded = data.get("wells", [])
        for w in wells_loaded:
            if isinstance(w.get("drill_date"), str):
                w["drill_date"] = date.fromisoformat(w["drill_date"])
        st.session_state.wells = wells_loaded

        capex_loaded = data.get("project_capex", [])
        for c in capex_loaded:
            if isinstance(c.get("date"), str):
                c["date"] = date.fromisoformat(c["date"])
        st.session_state.project_capex = capex_loaded

        maint_loaded = data.get("maintenance_items", [])
        for m in maint_loaded:
            if isinstance(m.get("start_date"), str):
                m["start_date"] = date.fromisoformat(m["start_date"])
            if isinstance(m.get("end_date"), str):
                m["end_date"] = date.fromisoformat(m["end_date"])
        st.session_state.maintenance_items = maint_loaded

        st.session_state.historic_data = {}
        st.session_state.model_run = False
        st.session_state.results = None
        st.success("Inputs loaded successfully!")
    except Exception as e:
        st.error(f"Error loading inputs: {e}")

def find_irr_robust(cashflows):
    annual_rates = np.linspace(-0.9, 5.0, 500)
    npv_values = np.array([npf.npv((1 + r) ** (1/12) - 1, cashflows) for r in annual_rates])
    signs = np.sign(npv_values)
    cross_idx = np.where(np.diff(signs))[0]
    if len(cross_idx) == 0:
        annual_rates = np.linspace(-0.99, 10.0, 1000)
        npv_values = np.array([npf.npv((1 + r) ** (1/12) - 1, cashflows) for r in annual_rates])
        signs = np.sign(npv_values)
        cross_idx = np.where(np.diff(signs))[0]
        if len(cross_idx) == 0:
            return None
    roots = []
    for idx in cross_idx:
        r1, r2 = annual_rates[idx], annual_rates[idx+1]
        v1, v2 = npv_values[idx], npv_values[idx+1]
        roots.append(r1 - v1 * (r2 - r1) / (v2 - v1))
    roots = [r for r in roots if r > -0.99]
    if not roots: return None
    positive_roots = [r for r in roots if 0 < r < 5.0]
    return min(positive_roots) if positive_roots else min(roots)

# Sidebar Inputs
st.sidebar.title("Petroleum Economics Dashboard")
project_type = st.sidebar.radio("Project Type", ["PSA with Contractor", "Government Self-Funded"], key="project_type")

if project_type == "Government Self-Funded":
    st.sidebar.info("Government pays all costs, receives all revenue. PSA parameters are automatically set.")
    royalty_pct = 0.0
    cost_recovery_ceiling = 1.0
    contractor_profit_share = 1.0
    amortize_capex = False
    amort_years = 5
else:
    royalty_val = st.sidebar.number_input("Royalty (%)", 0.0, 100.0,
                                          value=st.session_state.psa_royalty_pct * 100, step=0.1)
    st.session_state.psa_royalty_pct = royalty_val / 100.0
    royalty_pct = st.session_state.psa_royalty_pct

    cost_rec_val = st.sidebar.number_input("Cost Recovery Ceiling (%)", 0.0, 100.0,
                                           value=st.session_state.psa_cost_recovery_ceiling * 100, step=0.5)
    st.session_state.psa_cost_recovery_ceiling = cost_rec_val / 100.0
    cost_recovery_ceiling = st.session_state.psa_cost_recovery_ceiling

    profit_share_val = st.sidebar.number_input("Contractor Profit Oil Share (%)", 0.0, 100.0,
                                               value=st.session_state.psa_contractor_profit_share * 100, step=0.5)
    st.session_state.psa_contractor_profit_share = profit_share_val / 100.0
    contractor_profit_share = st.session_state.psa_contractor_profit_share

    st.sidebar.markdown("---")
    st.sidebar.subheader("Capex Amortisation")
    amortize_capex = st.sidebar.checkbox("Enable Capex Amortisation", value=False)
    amort_years = 5
    if amortize_capex:
        amort_years = st.sidebar.slider("Amortisation Period (years)", min_value=1, max_value=10, value=5, step=1)

mode = st.sidebar.radio("Production Mode", ["Forecast (decline curve)", "Historic / Actual production"], key="mode_radio")
col1, col2 = st.sidebar.columns(2)
start_date = col1.date_input("Start Date", date(2025, 6, 1), min_value=date(1900,1,1), max_value=date(9999,12,31), key="start")
end_date = col2.date_input("End Date", date(2030, 6, 1), min_value=date(1900,1,1), max_value=date(9999,12,31), key="end")
annual_discount_pct = st.sidebar.number_input("Annual Discount Rate (%)", 0.0, 30.0, 10.0, 0.1, key="annual_discount_pct") / 100.0
oil_prices = st.sidebar.multiselect("Oil Prices ($/bbl)", options=[40,50,60,70,80,90,100,110,120,150,200], default=[60,70,80], key="oil_prices")

# Save / Load
st.sidebar.markdown("---")
st.sidebar.subheader("Save / Load Inputs")
if st.sidebar.button("💾 Save Inputs"):
    inputs_json = json.dumps(serialise_inputs(), indent=2, default=str)
    st.sidebar.download_button("Download inputs.json", inputs_json, "psa_inputs.json", "application/json")
st.sidebar.file_uploader("Load Inputs (JSON)", type=["json"], key="load_json", on_change=load_inputs_callback)

# Wells
st.sidebar.markdown("---")
st.sidebar.subheader("Wells")
with st.sidebar.expander("Add / Edit Well", expanded=True):
    well_name = st.text_input("Well Name", "", key="well_name")
    capex = st.number_input("CapEx (MM$)", 0.0, 5000.0, 4.0, step=0.01, key="well_capex")
    drill_date = st.date_input("First Production Date", start_date, min_value=date(1900,1,1), max_value=date(9999,12,31), key="drill_date")
    opex_bbl = st.number_input("OpEx ($/bbl)", 0.0, 500.0, 10.0, step=0.01, key="well_opex")
    if mode == "Forecast (decline curve)":
        init_rate = st.number_input("Initial Rate (bbl/day)", 0.0, 100000.0, 400.0, step=10.0, key="init_rate")
        annual_decline = st.number_input("Annual Nominal Decline (%)", 0.0, 100.0, 15.0, step=0.01)
    else:
        uploaded_file = st.file_uploader("Upload Historic Production (CSV)", type=["csv"], key="hist_upload")
        if uploaded_file is not None:
            try:
                hist_df = pd.read_csv(uploaded_file)
                if 'date' not in hist_df.columns or 'gross_bbl' not in hist_df.columns:
                    st.error("CSV must have 'date' and 'gross_bbl' columns.")
                else:
                    hist_df['date'] = pd.to_datetime(hist_df['date']).dt.date
                    hist_df['month_start'] = hist_df['date'].apply(lambda d: date(d.year, d.month, 1))
                    hist_agg = hist_df.groupby('month_start')['gross_bbl'].sum().reset_index()
                    hist_agg.columns = ['date', 'gross_bbl']
                    if 'opex_per_bbl' in hist_df.columns:
                        avg_opex = hist_df.groupby('month_start')['opex_per_bbl'].mean().reset_index()
                        hist_agg = hist_agg.merge(avg_opex, on='date', how='left')
                    else:
                        hist_agg['opex_per_bbl'] = opex_bbl
                    st.session_state.historic_data[well_name] = hist_agg
                    st.success("Historic data loaded.")
            except Exception as e:
                st.error(f"Error reading CSV: {e}")
    if st.button("Add Well", key="add_well"):
        if not well_name:
            st.error("Please enter a well name.")
        elif mode == "Historic / Actual production" and well_name not in st.session_state.historic_data:
            st.error("Please upload a historic production CSV first.")
        else:
            new_well = {
                "name": well_name, "capex_mm": capex, "drill_date": drill_date,
                "opex_per_bbl": opex_bbl, "mode": "historic" if "Historic" in mode else "forecast"
            }
            if new_well["mode"] == "forecast":
                new_well["initial_rate"] = init_rate
                new_well["annual_decline_pct"] = annual_decline
            st.session_state.wells.append(new_well)
            st.success(f"Well '{well_name}' added.")

if st.session_state.wells:
    st.sidebar.subheader("Existing Wells")
    for i, well in enumerate(st.session_state.wells):
        col1, col2 = st.sidebar.columns([4,1])
        mode_tag = "📈" if well["mode"]=="forecast" else "📁"
        col1.write(f"{mode_tag} {well['name']} | ${well['capex_mm']:.2f}MM")
        if col2.button("❌", key=f"del_well_{i}"):
            if well["name"] in st.session_state.historic_data:
                del st.session_state.historic_data[well["name"]]
            st.session_state.wells.pop(i)
            st.rerun()

# Project Capex
st.sidebar.markdown("---")
st.sidebar.subheader("Project Capex")
with st.sidebar.expander("Add Project Capex"):
    desc = st.text_input("Description", key="p_desc")
    amt = st.number_input("Amount (MM$)", 0.0, 5000.0, 10.0, step=0.01, key="p_amt")
    pdate = st.date_input("Date", start_date, min_value=date(1900,1,1), max_value=date(9999,12,31), key="p_date")
    if st.button("Add Capex", key="add_pcapex") and desc:
        st.session_state.project_capex.append({"description": desc, "capex_mm": amt, "date": pdate})
        st.success("Added")
if st.session_state.project_capex:
    st.sidebar.subheader("Existing Project Capex")
    for i, item in enumerate(st.session_state.project_capex):
        col1, col2 = st.sidebar.columns([4,1])
        col1.write(f"{item['description']} | ${item['capex_mm']:.2f}MM | {item['date']}")
        if col2.button("❌", key=f"del_pcapex_{i}"):
            st.session_state.project_capex.pop(i)
            st.rerun()

# Maintenance (new: gross amount & date range)
st.sidebar.subheader("Maintenance (Opex)")
with st.sidebar.expander("Add Maintenance"):
    mdesc = st.text_input("Description (Maint)", key="m_desc")
    mamt_total = st.number_input("Gross Amount (MM$)", 0.0, 5000.0, 2.0, step=0.01, key="m_amt_total")
    col_m1, col_m2 = st.sidebar.columns(2)
    mstart = col_m1.date_input("Start Date", start_date, min_value=date(1900,1,1), max_value=date(9999,12,31), key="m_start")
    mend   = col_m2.date_input("End Date",   end_date,   min_value=date(1900,1,1), max_value=date(9999,12,31), key="m_end")
    if st.button("Add Maintenance", key="add_maint") and mdesc:
        st.session_state.maintenance_items.append({
            "description": mdesc,
            "gross_amount_mm": mamt_total,
            "start_date": mstart,
            "end_date": mend
        })
        st.success("Added")
if st.session_state.maintenance_items:
    st.sidebar.subheader("Existing Maintenance")
    for i, item in enumerate(st.session_state.maintenance_items):
        col1, col2 = st.sidebar.columns([4,1])
        col1.write(f"{item['description']} | ${item['gross_amount_mm']:.2f}MM | {item['start_date']} to {item['end_date']}")
        if col2.button("❌", key=f"del_maint_{i}"):
            st.session_state.maintenance_items.pop(i)
            st.rerun()

# ─── Main Page ───
st.title("Petroleum Economics Dashboard")
st.markdown("---")

if st.button("🚀 RUN MODEL", type="primary", use_container_width=True):
    if not st.session_state.wells:
        st.error("Add at least one well before running.")
    elif not oil_prices:
        st.error("Select at least one oil price scenario.")
    else:
        with st.spinner("Running PSA model..."):
            # Build monthly grid
            months = []
            curr = date(start_date.year, start_date.month, 1)
            while curr <= end_date:
                nxt = date(curr.year+1,1,1) if curr.month==12 else date(curr.year, curr.month+1,1)
                days = (nxt - curr).days
                months.append({"date": curr, "days": days})
                curr = nxt
            base_df = pd.DataFrame(months)
            base_df["gross_bbl"] = 0.0
            base_df["opex_mm"] = 0.0
            base_df["capex_mm"] = 0.0               # full capex (for economic metrics)
            base_df["capex_recoverable"] = 0.0      # amortised portion for cost recovery

            # Wells – production and opex
            for well in st.session_state.wells:
                well_start = date(well["drill_date"].year, well["drill_date"].month, 1)
                idx = base_df[base_df["date"] >= well_start].index.min()
                if pd.isna(idx): continue
                # Full capex recorded for cash flow
                base_df.loc[idx, "capex_mm"] += well["capex_mm"]

                if well["mode"] == "forecast":
                    rate = well["initial_rate"]
                    mf = monthly_decline_factor(well["annual_decline_pct"])
                    for i in range(idx, len(base_df)):
                        bbl = rate * base_df.loc[i,"days"]
                        base_df.loc[i,"gross_bbl"] += bbl
                        base_df.loc[i,"opex_mm"] += bbl * well["opex_per_bbl"] / 1e6
                        rate *= mf
                else:
                    if well["name"] in st.session_state.historic_data:
                        hist = st.session_state.historic_data[well["name"]]
                        merged = base_df[["date"]].merge(hist, on="date", how="left")
                        base_df["gross_bbl"] += merged["gross_bbl"].fillna(0)
                        opex_col = merged.get("opex_per_bbl", well["opex_per_bbl"])
                        base_df["opex_mm"] += (merged["gross_bbl"].fillna(0) * opex_col.fillna(0) / 1e6)

            # Project capex – also amortised if toggle ON
            for item in st.session_state.project_capex:
                item_date = date(item["date"].year, item["date"].month, 1)
                mask = base_df["date"] == item_date
                if mask.any():
                    base_df.loc[mask, "capex_mm"] += item["capex_mm"]

            # Maintenance – divide gross amount over months inclusive
            for item in st.session_state.maintenance_items:
                s = date(item["start_date"].year, item["start_date"].month, 1)
                e = date(item["end_date"].year, item["end_date"].month, 1)
                # compute inclusive months
                n_months = (e.year - s.year) * 12 + (e.month - s.month) + 1
                if n_months <= 0: n_months = 1
                monthly_amt = item["gross_amount_mm"] / n_months
                # add to opex for each month in the range
                for i, row in base_df.iterrows():
                    if row["date"] >= s and row["date"] <= e:
                        base_df.loc[i, "opex_mm"] += monthly_amt

            # Amortise capex for cost recovery
            # 1. Wells
            for well in st.session_state.wells:
                if amortize_capex:
                    well_start = date(well["drill_date"].year, well["drill_date"].month, 1)
                    n_months = amort_years * 12
                    monthly_slice = well["capex_mm"] / n_months
                    for i in range(len(base_df)):
                        if base_df.loc[i, "date"] >= well_start:
                            months_from_start = (base_df.loc[i, "date"].year - well_start.year)*12 + (base_df.loc[i, "date"].month - well_start.month)
                            if months_from_start < n_months:
                                base_df.loc[i, "capex_recoverable"] += monthly_slice
                else:
                    well_start = date(well["drill_date"].year, well["drill_date"].month, 1)
                    idx = base_df[base_df["date"] == well_start].index.min() if well_start in base_df["date"].values else None
                    if idx is not None:
                        base_df.loc[idx, "capex_recoverable"] += well["capex_mm"]

            # 2. Project capex
            for item in st.session_state.project_capex:
                pstart = date(item["date"].year, item["date"].month, 1)
                if amortize_capex:
                    n_months = amort_years * 12
                    monthly_slice = item["capex_mm"] / n_months
                    for i in range(len(base_df)):
                        if base_df.loc[i, "date"] >= pstart:
                            months_from_start = (base_df.loc[i, "date"].year - pstart.year)*12 + (base_df.loc[i, "date"].month - pstart.month)
                            if months_from_start < n_months:
                                base_df.loc[i, "capex_recoverable"] += monthly_slice
                else:
                    mask = base_df["date"] == pstart
                    if mask.any():
                        base_df.loc[mask, "capex_recoverable"] += item["capex_mm"]

            base_df["total_cost"] = base_df["opex_mm"] + base_df["capex_recoverable"]

            monthly_discount_rate = (1 + annual_discount_pct) ** (1/12) - 1
            discount_factors = np.array([(1+monthly_discount_rate)**(-(i+1)) for i in range(len(base_df))])

            results_dict = {}
            for price in oil_prices:
                df = base_df.copy()
                unrecovered = 0.0
                cg_list, gg_list, unrec_list = [], [], []
                cn_list, gn_list = [], []
                for i in range(len(df)):
                    gross_rev = df.loc[i, "gross_bbl"] * price / 1e6
                    royalty = gross_rev * royalty_pct
                    after_royalty = gross_rev - royalty
                    max_co = after_royalty * cost_recovery_ceiling
                    recoverable = df.loc[i, "total_cost"] + unrecovered
                    cost_oil = min(max_co, recoverable)
                    profit_oil = after_royalty - cost_oil
                    cg = cost_oil + profit_oil * contractor_profit_share
                    gg = royalty + profit_oil * (1 - contractor_profit_share)
                    cg_list.append(cg); gg_list.append(gg)
                    unrecovered = recoverable - cost_oil
                    unrec_list.append(unrecovered)
                    # Net cash flow uses full capex (capex_mm) not amortised
                    actual_cost = df.loc[i, "opex_mm"] + df.loc[i, "capex_mm"]
                    cn_list.append(cg - actual_cost)
                    gn_list.append(gg)

                df["contractor_gross_mm"] = cg_list
                df["government_gross_mm"] = gg_list
                df["unrecovered_mm"] = unrec_list
                df["contractor_net_cf"] = cn_list
                df["government_cf"] = gn_list
                df["pv_contractor"] = df["contractor_net_cf"] * discount_factors
                df["pv_government"] = df["government_cf"] * discount_factors
                df["cum_contractor_cf"] = df["contractor_net_cf"].cumsum()
                df["cum_government_cf"] = df["government_cf"].cumsum()
                df["cum_pv_contractor"] = df["pv_contractor"].cumsum()
                df["cum_pv_government"] = df["pv_government"].cumsum()

                total_con_npv = df["pv_contractor"].sum()
                total_gov_npv = df["pv_government"].sum()
                contractor_irr = find_irr_robust(df["contractor_net_cf"].values)

                payback_months = None
                cum_pos = df[df["cum_contractor_cf"] > 0]
                if not cum_pos.empty:
                    payback_months = int(cum_pos.index[0])

                net_profit = df["contractor_net_cf"].sum()
                investment = df["capex_mm"].sum() + df["opex_mm"].sum()   # total investment
                roi = (net_profit / investment * 100) if investment != 0 else None

                df["price"] = price
                df["scenario"] = f"{price} $/bbl"
                df["contractor_npv"] = total_con_npv
                df["government_npv"] = total_gov_npv
                df["contractor_irr"] = contractor_irr
                df["payback_months"] = payback_months
                df["contractor_roi"] = roi
                results_dict[price] = df

            st.session_state.results = results_dict
            st.session_state.model_run = True
            st.success("Model run completed!")

# ─── Results Display (identical to previous version, with cum_pv in Excel) ───
if st.session_state.model_run and st.session_state.results is not None:
    results = st.session_state.results
    contractor_label = "Government" if project_type == "Government Self-Funded" else "Contractor"
    gov_label = "Government (nil)" if project_type == "Government Self-Funded" else "Government"

    tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboards", "📈 Production & Costs", "📋 Data Tables", "💾 Excel Export"])

    with tab1:
        st.subheader(f"{contractor_label} vs {gov_label} Cash Flows")
        all_dfs = []
        for price, df in results.items():
            all_dfs.append(df.assign(price=price))
        full_df = pd.concat(all_dfs)

        col1, col2 = st.columns(2)
        with col1:
            fig1 = px.line(full_df, x="date", y="contractor_net_cf", color="scenario",
                           title=f"{contractor_label} Net Cash Flow (MM$)")
            st.plotly_chart(fig1, use_container_width=True)
        with col2:
            fig2 = px.line(full_df, x="date", y="government_cf", color="scenario",
                           title=f"{gov_label} Cash Flow (MM$)")
            st.plotly_chart(fig2, use_container_width=True)

        col3, col4 = st.columns(2)
        with col3:
            fig3 = px.line(full_df, x="date", y="cum_contractor_cf", color="scenario",
                           title=f"Cumulative {contractor_label} Net Cash Flow (MM$)")
            st.plotly_chart(fig3, use_container_width=True)
        with col4:
            fig4 = px.line(full_df, x="date", y="cum_government_cf", color="scenario",
                           title=f"Cumulative {gov_label} Cash Flow (MM$)")
            st.plotly_chart(fig4, use_container_width=True)

        st.subheader("NPV Comparison")
        npv_list = []
        for price, df in results.items():
            npv_list.append({
                "Scenario": f"{price} $/bbl",
                f"{contractor_label} NPV (MM$)": df["contractor_npv"].iloc[0],
                f"{gov_label} NPV (MM$)": df["government_npv"].iloc[0]
            })
        npv_df = pd.DataFrame(npv_list)
        fig5 = go.Figure(data=[
            go.Bar(name=contractor_label, x=npv_df["Scenario"], y=npv_df[f"{contractor_label} NPV (MM$)"]),
            go.Bar(name=gov_label, x=npv_df["Scenario"], y=npv_df[f"{gov_label} NPV (MM$)"])
        ])
        fig5.update_layout(barmode='group', title="NPV by Scenario")
        st.plotly_chart(fig5, use_container_width=True)

        st.subheader("Key Metrics by Scenario")
        metrics_list = []
        for price, df in results.items():
            irr_val = df["contractor_irr"].iloc[0]
            roi_val = df["contractor_roi"].iloc[0]
            metrics_list.append({
                "Oil Price ($/bbl)": price,
                f"{contractor_label} NPV (MM$)": round(df["contractor_npv"].iloc[0], 2),
                f"{gov_label} NPV (MM$)": round(df["government_npv"].iloc[0], 2),
                f"{contractor_label} IRR (%)": round(irr_val * 100, 2) if irr_val is not None else "N/A",
                "Payback (months)": df["payback_months"].iloc[0] if df["payback_months"].iloc[0] else "N/A",
                f"{contractor_label} ROI (%)": round(roi_val, 2) if roi_val else "N/A"
            })
        st.dataframe(pd.DataFrame(metrics_list), use_container_width=True)

    with tab2:
        st.subheader("Monthly Production and Costs")
        first_price = list(results.keys())[0]
        base = results[first_price][["date", "days", "gross_bbl", "opex_mm", "capex_mm", "capex_recoverable", "total_cost"]].copy()
        st.dataframe(base.style.format({
            "gross_bbl": "{:,.0f}", "opex_mm": "{:,.2f}", "capex_mm": "{:,.2f}",
            "capex_recoverable": "{:,.2f}", "total_cost": "{:,.2f}"
        }), use_container_width=True)
        fig_p = go.Figure()
        fig_p.add_trace(go.Scatter(x=base["date"], y=base["gross_bbl"], name="Gross Production (bbl)"))
        fig_p.add_trace(go.Bar(x=base["date"], y=base["total_cost"], name="Total Cost (MM$)", yaxis="y2"))
        fig_p.update_layout(yaxis=dict(title="Barrels"), yaxis2=dict(title="MM$", overlaying="y", side="right"),
                            title="Production & Total Cost")
        st.plotly_chart(fig_p, use_container_width=True)

    with tab3:
        st.subheader("Monthly Cash Flow Details")
        selected_price = st.selectbox("Select oil price scenario:", options=list(results.keys()),
                                      format_func=lambda x: f"{x} $/bbl")
        df_show = results[selected_price][[
            "date", "gross_bbl", "contractor_gross_mm", "government_gross_mm",
            "unrecovered_mm", "contractor_net_cf", "government_cf",
            "pv_contractor", "pv_government", "cum_contractor_cf", "cum_government_cf",
            "cum_pv_contractor", "cum_pv_government"
        ]].copy()
        st.dataframe(df_show.style.format({
            "gross_bbl": "{:,.0f}", "contractor_gross_mm": "{:,.2f}", "government_gross_mm": "{:,.2f}",
            "unrecovered_mm": "{:,.2f}", "contractor_net_cf": "{:,.2f}", "government_cf": "{:,.2f}",
            "pv_contractor": "{:,.2f}", "pv_government": "{:,.2f}",
            "cum_contractor_cf": "{:,.2f}", "cum_government_cf": "{:,.2f}",
            "cum_pv_contractor": "{:,.2f}", "cum_pv_government": "{:,.2f}"
        }), use_container_width=True)

    with tab4:
        st.subheader("Download Results & Package")
        if st.button("📥 Generate Excel Report"):
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                summary_list = []
                for price, df in results.items():
                    irr_val = df["contractor_irr"].iloc[0]
                    roi_val = df["contractor_roi"].iloc[0]
                    summary_list.append({
                        "Oil Price ($/bbl)": price,
                        f"{contractor_label} NPV (MM$)": df["contractor_npv"].iloc[0],
                        f"{gov_label} NPV (MM$)": df["government_npv"].iloc[0],
                        f"{contractor_label} IRR (%)": (irr_val * 100) if irr_val else "N/A",
                        "Payback (months)": df["payback_months"].iloc[0] if df["payback_months"].iloc[0] else "N/A",
                        f"{contractor_label} ROI (%)": round(roi_val, 2) if roi_val else "N/A",
                        f"Total {contractor_label} Gross (MM$)": df["contractor_gross_mm"].sum(),
                        f"Total {gov_label} Gross (MM$)": df["government_gross_mm"].sum(),
                        "Total Cost (MM$)": df["total_cost"].sum()
                    })
                pd.DataFrame(summary_list).to_excel(writer, sheet_name="Summary", index=False)
                for price, df in results.items():
                    tab_name = f"Oil_{price}$"
                    export_df = df[["date", "days", "gross_bbl", "opex_mm", "capex_mm", "capex_recoverable", "total_cost",
                                    "unrecovered_mm", "contractor_gross_mm", "government_gross_mm",
                                    "contractor_net_cf", "government_cf", "pv_contractor", "pv_government",
                                    "cum_contractor_cf", "cum_government_cf",
                                    "cum_pv_contractor", "cum_pv_government"]].copy()
                    export_df.to_excel(writer, sheet_name=tab_name, index=False)
            output.seek(0)
            st.download_button("📥 Download Excel", data=output, file_name="psa_results.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        if st.button("📦 Download Full Output Package (ZIP)"):
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                # Excel
                excel_buffer = BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                    summary_list = []
                    for price, df in results.items():
                        irr_val = df["contractor_irr"].iloc[0]
                        roi_val = df["contractor_roi"].iloc[0]
                        summary_list.append({
                            "Oil Price ($/bbl)": price,
                            f"{contractor_label} NPV (MM$)": df["contractor_npv"].iloc[0],
                            f"{gov_label} NPV (MM$)": df["government_npv"].iloc[0],
                            f"{contractor_label} IRR (%)": (irr_val * 100) if irr_val else "N/A",
                            "Payback (months)": df["payback_months"].iloc[0] if df["payback_months"].iloc[0] else "N/A",
                            f"{contractor_label} ROI (%)": round(roi_val, 2) if roi_val else "N/A",
                            f"Total {contractor_label} Gross (MM$)": df["contractor_gross_mm"].sum(),
                            f"Total {gov_label} Gross (MM$)": df["government_gross_mm"].sum(),
                            "Total Cost (MM$)": df["total_cost"].sum()
                        })
                    pd.DataFrame(summary_list).to_excel(writer, sheet_name="Summary", index=False)
                    for price, df in results.items():
                        tab_name = f"Oil_{price}$"
                        export_df = df[["date", "days", "gross_bbl", "opex_mm", "capex_mm", "capex_recoverable", "total_cost",
                                        "unrecovered_mm", "contractor_gross_mm", "government_gross_mm",
                                        "contractor_net_cf", "government_cf", "pv_contractor", "pv_government",
                                        "cum_contractor_cf", "cum_government_cf",
                                        "cum_pv_contractor", "cum_pv_government"]].copy()
                        export_df.to_excel(writer, sheet_name=tab_name, index=False)
                excel_buffer.seek(0)
                zf.writestr("psa_results.xlsx", excel_buffer.read())

                # Inputs
                inputs_json = json.dumps(serialise_inputs(), indent=2, default=str)
                zf.writestr("model_inputs.json", inputs_json)

                                                # --- Robust chart export (PNG optional, HTML always included) ---
                all_dfs = []
                for price, df in results.items():
                    all_dfs.append(df.assign(price=price))
                full_df = pd.concat(all_dfs)

                # Build all five charts
                fig1 = px.line(full_df, x="date", y="contractor_net_cf", color="scenario",
                               title=f"{contractor_label} Net Cash Flow")
                fig2 = px.line(full_df, x="date", y="government_cf", color="scenario",
                               title=f"{gov_label} Cash Flow")
                fig3 = px.line(full_df, x="date", y="cum_contractor_cf", color="scenario",
                               title=f"Cumulative {contractor_label} Net Cash Flow")
                fig4 = px.line(full_df, x="date", y="cum_government_cf", color="scenario",
                               title=f"Cumulative {gov_label} Cash Flow")

                npv_list = []
                for price, df in results.items():
                    npv_list.append({
                        "Scenario": f"{price} $/bbl",
                        f"{contractor_label} NPV": df["contractor_npv"].iloc[0],
                        f"{gov_label} NPV": df["government_npv"].iloc[0]
                    })
                npv_df = pd.DataFrame(npv_list)
                fig5 = go.Figure(data=[
                    go.Bar(name=contractor_label, x=npv_df["Scenario"], y=npv_df[f"{contractor_label} NPV"]),
                    go.Bar(name=gov_label, x=npv_df["Scenario"], y=npv_df[f"{gov_label} NPV"])
                ])
                fig5.update_layout(barmode='group', title="NPV by Scenario")

                # Try to add PNGs (kaleido required)
                png_success = False
                try:
                    for name, fig in [("contractor_net_cf", fig1), ("government_cf", fig2),
                                      ("cum_contractor_cf", fig3), ("cum_government_cf", fig4),
                                      ("npv", fig5)]:
                        img_buf = BytesIO()
                        fig.write_image(img_buf, format="png", engine="kaleido")
                        zf.writestr(f"chart_{name}.png", img_buf.getvalue())
                    png_success = True
                except Exception:
                    pass

                # Always write interactive HTML (works without kaleido)
                try:
                    html_str = "<html><body>"
                    html_str += pio.to_html(fig1, full_html=False, include_plotlyjs='cdn')
                    html_str += pio.to_html(fig2, full_html=False, include_plotlyjs='cdn')
                    html_str += pio.to_html(fig3, full_html=False, include_plotlyjs='cdn')
                    html_str += pio.to_html(fig4, full_html=False, include_plotlyjs='cdn')
                    html_str += pio.to_html(fig5, full_html=False, include_plotlyjs='cdn')
                    html_str += "</body></html>"
                    zf.writestr("charts.html", html_str)
                except Exception:
                    pass

                # Informative README
                msg = "Interactive charts: charts.html (open in browser)."
                if png_success:
                    msg += " PNG images also included."
                else:
                    msg += " PNG export requires kaleido (not installed)."
                zf.writestr("charts_README.txt", msg)

            zip_buffer.seek(0)
            st.download_button("📦 Download ZIP Package", data=zip_buffer, file_name="psa_output_package.zip",
                               mime="application/zip")

st.sidebar.markdown("---")
st.sidebar.caption("Petroleum Economics by Youssif Sebak.")
