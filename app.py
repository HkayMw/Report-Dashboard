import streamlit as st
import pandas as pd
import plotly.express as px

from data_processing import load_raw, clean_data, find_fuzzy_suggestions, prepare_raw_columns
from report_generator import build_report
from google_sheets import fetch_google_sheet, load_saved_url, save_url
from column_mapping import (
    suggest_mapping, load_saved_mapping, save_mapping, apply_mapping, REQUIRED_FIELDS
)
from auth import verify_pin, set_pin, is_default_pin_active

st.set_page_config(page_title="NID & NRBC Daily Reporting Dashboard", layout="wide")
st.title("🧾 NID & NRBC Daily Reporting Dashboard")
st.caption(
    "Note: Birth Registration and National ID (NID) Registration are "
    "two separate activities captured on the same form — they're tracked "
    "side-by-side below, not as a before/after pipeline."
)

# ------------------------------------------------------------------
# Session state init
# ------------------------------------------------------------------
if "confirmed_merges" not in st.session_state:
    st.session_state.confirmed_merges = {"zone": {}, "center": {}}
if "sheet_refresh_token" not in st.session_state:
    st.session_state.sheet_refresh_token = 0
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

# ------------------------------------------------------------------
# Admin login (sidebar)
# ------------------------------------------------------------------
st.sidebar.header("Admin")
if st.session_state.is_admin:
    st.sidebar.success("Admin mode active.")
    if st.sidebar.button("Log out"):
        st.session_state.is_admin = False
        st.rerun()
    if is_default_pin_active():
        st.sidebar.warning("Still using the default PIN (1234) — change it below.")
    with st.sidebar.expander("Change admin PIN"):
        new_pin = st.text_input("New PIN", type="password", key="new_pin_input")
        if st.button("Update PIN"):
            if new_pin and len(new_pin) >= 4:
                set_pin(new_pin)
                st.sidebar.success("PIN updated.")
            else:
                st.sidebar.error("PIN must be at least 4 characters.")
else:
    pin_attempt = st.sidebar.text_input("Enter admin PIN", type="password", key="pin_attempt")
    if st.sidebar.button("Log in"):
        if verify_pin(pin_attempt):
            st.session_state.is_admin = True
            st.rerun()
        else:
            st.sidebar.error("Incorrect PIN.")

is_admin = st.session_state.is_admin

# ------------------------------------------------------------------
# Data source: Google Sheet (live) or manual upload
# Admins configure the connection; everyone else just gets the data it
# already points to, read-only, so viewers can't interfere with the link
# or accidentally trigger uploads.
# ------------------------------------------------------------------
raw_df = None
saved_url = load_saved_url()

if is_admin:
    st.subheader("Data Source (admin)")
    source_mode = st.radio(
        "Where should the data come from?",
        ["Google Sheet (live)", "Upload Excel file"],
        horizontal=True,
    )

    if source_mode == "Google Sheet (live)":
        sheet_url = st.text_input(
            "Google Sheet URL (must be shared as 'Anyone with the link can view')",
            value=saved_url,
            placeholder="https://docs.google.com/spreadsheets/d/....../edit",
        )
        col_a, col_b = st.columns([1, 4])
        refresh_clicked = col_a.button("🔄 Refresh now")

        if sheet_url:
            if sheet_url != saved_url:
                save_url(sheet_url)
            if refresh_clicked:
                st.session_state.sheet_refresh_token += 1

            @st.cache_data(ttl=300, show_spinner="Fetching latest responses from Google Sheets...")
            def _cached_fetch(url, _refresh_token):
                return fetch_google_sheet(url)

            try:
                fetched = _cached_fetch(sheet_url, st.session_state.sheet_refresh_token)
                raw_df = prepare_raw_columns(fetched)
                col_b.success(f"Loaded {len(raw_df)} rows from Google Sheets.", icon="✅")
            except RuntimeError as e:
                st.error(str(e))
        else:
            st.info("Paste the Google Sheet URL above to load data automatically.")
    else:
        st.caption("Note: an uploaded file is only visible in this admin session — "
                   "regular viewers will still see the connected Google Sheet, not this upload.")
        uploaded_file = st.file_uploader("Upload the Excel file (.xlsx)", type=["xlsx"])
        if uploaded_file is not None:
            raw_df = load_raw(uploaded_file)

else:
    # Viewer path: silently use whatever the admin has already connected.
    if saved_url:
        @st.cache_data(ttl=300, show_spinner="Loading latest data...")
        def _cached_fetch_viewer(url):
            return fetch_google_sheet(url)

        try:
            fetched = _cached_fetch_viewer(saved_url)
            raw_df = prepare_raw_columns(fetched)
        except RuntimeError as e:
            st.error(f"Couldn't load the connected data source: {e}")
    else:
        st.info("No data source has been connected yet. An admin needs to log in and connect one.")

if raw_df is None:
    st.stop()

# ------------------------------------------------------------------
# Column mapping — makes the app resilient to header drift (renamed columns,
# reordered columns, minor wording changes) instead of requiring exact names.
# This is part of the data connection setup, so it's admin-only; viewers rely
# on whatever mapping has already been confirmed and saved.
# ------------------------------------------------------------------
raw_headers = list(raw_df.columns)
saved_mapping = load_saved_mapping()

mapping = {h: c for h, c in saved_mapping.items() if h in raw_headers}
already_mapped_canonicals = set(mapping.values())

confident, suggestions, unmapped = suggest_mapping(
    [h for h in raw_headers if h not in mapping]
)
for h, c in confident.items():
    if c not in already_mapped_canonicals:
        mapping[h] = c
        already_mapped_canonicals.add(c)

needs_review = {
    c: cands for c, cands in suggestions.items()
    if c not in already_mapped_canonicals
}

if needs_review or len(mapping) < len(REQUIRED_FIELDS):
    if is_admin:
        with st.expander("🗂️ Column Mapping — please confirm", expanded=True):
            st.write(
                "Some columns couldn't be confidently matched to what this app expects. "
                "Pick the right source column for each field below (or 'None' to skip)."
            )
            options = ["(none)"] + raw_headers
            for canonical in REQUIRED_FIELDS:
                if canonical in already_mapped_canonicals:
                    continue
                candidates = suggestions.get(canonical, [])
                default_idx = 0
                if candidates:
                    best_header = candidates[0][0]
                    if best_header in options:
                        default_idx = options.index(best_header)
                choice = st.selectbox(
                    f"**{canonical}**" + (f"  _(best guess: {candidates[0][0]}, {candidates[0][1]:.0%} similar)_" if candidates else "  _(no match found)_"),
                    options, index=default_idx, key=f"map_{canonical}"
                )
                if choice != "(none)":
                    mapping[choice] = canonical

            if st.button("Confirm mapping"):
                save_mapping(mapping)
                st.rerun()
    else:
        st.warning("The data connection needs setup by an admin before this can be shown.")
        st.stop()

missing_required = [c for c in REQUIRED_FIELDS if c not in mapping.values()]
if missing_required:
    if is_admin:
        st.error(f"Missing required column(s): {', '.join(missing_required)}. Please map them above.")
    else:
        st.warning("The data connection needs setup by an admin before this can be shown.")
    st.stop()

raw_df = apply_mapping(raw_df, mapping)
cleaned_df, report = clean_data(raw_df, st.session_state.confirmed_merges)

# ------------------------------------------------------------------
# Data quality review panel — informational parts stay visible to everyone;
# the merge controls (which edit the data) are admin-only.
# ------------------------------------------------------------------
with st.expander("🔍 Data Quality Review", expanded=False):
    st.write(f"**Duplicate rows auto-removed (kept latest by timestamp):** {report['duplicates_removed_count']}")
    if len(report["duplicates_found"]):
        st.dataframe(report["duplicates_found"][["Timestamp", "ZONE NAME", "CENTER NAME", "Date"]])

    if len(report["missing_numeric_rows"]):
        st.warning(f"{len(report['missing_numeric_rows'])} row(s) had missing numeric values (filled with 0).")
        st.dataframe(report["missing_numeric_rows"])

    if len(report["negative_rows"]):
        st.warning(f"{len(report['negative_rows'])} row(s) had negative numbers.")
        st.dataframe(report["negative_rows"])

    if len(report["non_integer_rows"]):
        st.warning(
            f"{len(report['non_integer_rows'])} row(s) have a non-whole-number value "
            "(e.g. 0.01) in a count column — these are almost certainly data entry "
            "errors and are worth checking with the reporting center."
        )
        st.dataframe(report["non_integer_rows"][
            ["ZONE NAME", "CENTER NAME", "Date"] + [
                c for c in ["Total Male Births Registered", "Total Female Births Registered",
                            "Total Males Processed", "Total Females Processed"]
            ]
        ])

    if is_admin:
        st.subheader("Possible Zone Name Typos")
        zone_suggestions = find_fuzzy_suggestions(cleaned_df, "ZONE NAME")
        if not zone_suggestions:
            st.write("No suspected typos found among zone names.")
        for s in zone_suggestions:
            c1, c2, c3 = st.columns([3, 1, 2])
            c1.write(f"**{s['a']}**  ↔  **{s['b']}**  (similarity {s['score']})")
            choice = c2.radio(
                "Merge?", ["Keep separate", f"{s['a']} → {s['b']}", f"{s['b']} → {s['a']}"],
                key=f"zone_{s['a']}_{s['b']}", label_visibility="collapsed"
            )
            if choice == f"{s['a']} → {s['b']}":
                st.session_state.confirmed_merges["zone"][s["a"]] = s["b"]
            elif choice == f"{s['b']} → {s['a']}":
                st.session_state.confirmed_merges["zone"][s["b"]] = s["a"]
            else:
                st.session_state.confirmed_merges["zone"].pop(s["a"], None)
                st.session_state.confirmed_merges["zone"].pop(s["b"], None)

        st.subheader("Possible Center Name Typos")
        center_suggestions = find_fuzzy_suggestions(cleaned_df, "CENTER NAME")
        if not center_suggestions:
            st.write("No suspected typos found among center names.")
        for s in center_suggestions:
            c1, c2, c3 = st.columns([3, 1, 2])
            c1.write(f"**{s['a']}**  ↔  **{s['b']}**  (similarity {s['score']})")
            choice = c2.radio(
                "Merge?", ["Keep separate", f"{s['a']} → {s['b']}", f"{s['b']} → {s['a']}"],
                key=f"center_{s['a']}_{s['b']}", label_visibility="collapsed"
            )
            if choice == f"{s['a']} → {s['b']}":
                st.session_state.confirmed_merges["center"][s["a"]] = s["b"]
            elif choice == f"{s['b']} → {s['a']}":
                st.session_state.confirmed_merges["center"][s["b"]] = s["a"]
            else:
                st.session_state.confirmed_merges["center"].pop(s["a"], None)
                st.session_state.confirmed_merges["center"].pop(s["b"], None)

        if st.button("Apply confirmed merges"):
            st.rerun()

# ------------------------------------------------------------------
# Sidebar filters — available to everyone, viewing only
# ------------------------------------------------------------------
st.sidebar.header("Filters")
zones = sorted(cleaned_df["ZONE NAME"].unique())
selected_zones = st.sidebar.multiselect("Zone", zones, default=zones)

centers_available = sorted(cleaned_df[cleaned_df["ZONE NAME"].isin(selected_zones)]["CENTER NAME"].unique())
selected_centers = st.sidebar.multiselect("Center", centers_available, default=centers_available)

min_date = cleaned_df["Date"].min().date()
max_date = cleaned_df["Date"].max().date()
date_range = st.sidebar.date_input("Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = min_date, max_date

filtered = cleaned_df[
    cleaned_df["ZONE NAME"].isin(selected_zones)
    & cleaned_df["CENTER NAME"].isin(selected_centers)
    & (cleaned_df["Date"].dt.date >= start_date)
    & (cleaned_df["Date"].dt.date <= end_date)
]

if filtered.empty:
    st.warning("No data matches the current filters.")
    st.stop()

# ------------------------------------------------------------------
# KPIs — two independent activities, reported side by side
# ------------------------------------------------------------------
total_birth = int(filtered["Total Birth Registrations"].sum())
total_male_birth = int(filtered["Total Male Births Registered"].sum())
total_female_birth = int(filtered["Total Female Births Registered"].sum())

total_nid = int(filtered["Total NID Registrations"].sum())
total_male_nid = int(filtered["Total Males Processed"].sum())
total_female_nid = int(filtered["Total Females Processed"].sum())

st.subheader("Birth Registration")
b1, b2, b3 = st.columns(3)
b1.metric("Total Birth Registrations", f"{total_birth:,}")
b2.metric("Male", f"{total_male_birth:,}")
b3.metric("Female", f"{total_female_birth:,}")

st.subheader("National ID (NID) Registration")
n1, n2, n3 = st.columns(3)
n1.metric("Total NID Registrations", f"{total_nid:,}")
n2.metric("Male", f"{total_male_nid:,}")
n3.metric("Female", f"{total_female_nid:,}")

st.divider()

# ------------------------------------------------------------------
# Per-zone / per-center summaries
# ------------------------------------------------------------------
zone_summary = filtered.groupby("ZONE NAME").agg(
    Total_Birth_Registrations=("Total Birth Registrations", "sum"),
    Male_Births=("Total Male Births Registered", "sum"),
    Female_Births=("Total Female Births Registered", "sum"),
    Total_NID_Registrations=("Total NID Registrations", "sum"),
    Male_NID=("Total Males Processed", "sum"),
    Female_NID=("Total Females Processed", "sum"),
).sort_values("Total_Birth_Registrations", ascending=False)
zone_summary.columns = [
    "Total Birth Registrations", "Male Births", "Female Births",
    "Total NID Registrations", "Male NID", "Female NID",
]

center_summary = filtered.groupby("CENTER NAME").agg(
    Total_Birth_Registrations=("Total Birth Registrations", "sum"),
    Male_Births=("Total Male Births Registered", "sum"),
    Female_Births=("Total Female Births Registered", "sum"),
    Total_NID_Registrations=("Total NID Registrations", "sum"),
    Male_NID=("Total Males Processed", "sum"),
    Female_NID=("Total Females Processed", "sum"),
).sort_values("Total_Birth_Registrations", ascending=False)
center_summary.columns = [
    "Total Birth Registrations", "Male Births", "Female Births",
    "Total NID Registrations", "Male NID", "Female NID",
]

# ------------------------------------------------------------------
# Charts — visible to everyone
# ------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs(["By Zone", "By Center", "Trend Over Time", "Male vs Female"])

with tab1:
    fig = px.bar(zone_summary.reset_index(), x="ZONE NAME", y="Total Birth Registrations",
                 title="Birth Registrations by Zone", text_auto=True)
    st.plotly_chart(fig, use_container_width=True)

    fig_nid = px.bar(zone_summary.reset_index(), x="ZONE NAME", y="Total NID Registrations",
                      title="NID Registrations by Zone", text_auto=True)
    st.plotly_chart(fig_nid, use_container_width=True)

    st.dataframe(zone_summary)

with tab2:
    top_n = st.slider("Show top N centers", 5, min(50, len(center_summary)), min(15, len(center_summary)))
    top_centers = center_summary.head(top_n)
    fig = px.bar(top_centers.reset_index(), x="CENTER NAME", y="Total Birth Registrations",
                 title=f"Birth Registrations — Top {top_n} Centers", text_auto=True)
    fig.update_xaxes(tickangle=45)
    st.plotly_chart(fig, use_container_width=True)

    fig_nid = px.bar(top_centers.reset_index(), x="CENTER NAME", y="Total NID Registrations",
                      title=f"NID Registrations — Top {top_n} Centers", text_auto=True)
    fig_nid.update_xaxes(tickangle=45)
    st.plotly_chart(fig_nid, use_container_width=True)

    st.dataframe(center_summary)

with tab3:
    trend = filtered.groupby(filtered["Date"].dt.date).agg(
        Total_Birth_Registrations=("Total Birth Registrations", "sum"),
        Total_NID_Registrations=("Total NID Registrations", "sum"),
    ).reset_index()
    trend.columns = ["Date", "Total Birth Registrations", "Total NID Registrations"]
    if len(trend) > 1:
        fig = px.line(trend, x="Date", y=["Total Birth Registrations", "Total NID Registrations"], markers=True,
                      title="Birth & NID Registrations Over Time")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Only one date present in the current filter — trend chart will populate as more days are added.")
    st.dataframe(trend)

with tab4:
    st.markdown("**Birth Registration — Male vs Female**")
    zone_mf_birth = filtered.groupby("ZONE NAME").agg(
        Male=("Total Male Births Registered", "sum"),
        Female=("Total Female Births Registered", "sum"),
    ).reset_index()
    fig = px.bar(zone_mf_birth, x="ZONE NAME", y=["Male", "Female"], barmode="stack",
                 title="Birth Registrations by Zone — Male vs Female")
    st.plotly_chart(fig, use_container_width=True)

    overall_birth = pd.DataFrame({"Sex": ["Male", "Female"], "Count": [total_male_birth, total_female_birth]})
    fig_pie = px.pie(overall_birth, names="Sex", values="Count", title="Overall Birth Registration Male/Female Split")
    st.plotly_chart(fig_pie, use_container_width=True)

    st.markdown("**NID Registration — Male vs Female**")
    zone_mf_nid = filtered.groupby("ZONE NAME").agg(
        Male=("Total Males Processed", "sum"),
        Female=("Total Females Processed", "sum"),
    ).reset_index()
    fig2 = px.bar(zone_mf_nid, x="ZONE NAME", y=["Male", "Female"], barmode="stack",
                  title="NID Registrations by Zone — Male vs Female")
    st.plotly_chart(fig2, use_container_width=True)

    overall_nid = pd.DataFrame({"Sex": ["Male", "Female"], "Count": [total_male_nid, total_female_nid]})
    fig_pie2 = px.pie(overall_nid, names="Sex", values="Count", title="Overall NID Registration Male/Female Split")
    st.plotly_chart(fig_pie2, use_container_width=True)

st.divider()

# ------------------------------------------------------------------
# Export — admin only
# ------------------------------------------------------------------
if is_admin:
    st.subheader("📤 Export Report (admin)")
    kpis = {
        "Total Birth Registrations": total_birth,
        "Male Births": total_male_birth,
        "Female Births": total_female_birth,
        "Total NID Registrations": total_nid,
        "Male NID": total_male_nid,
        "Female NID": total_female_nid,
        "Number of Zones": filtered["ZONE NAME"].nunique(),
        "Number of Centers": filtered["CENTER NAME"].nunique(),
        "Date Range": f"{start_date} to {end_date}",
    }

    excel_bytes = build_report(filtered, report, zone_summary, center_summary, kpis)
    st.download_button(
        "Download Excel Report",
        data=excel_bytes,
        file_name="nrbc_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
