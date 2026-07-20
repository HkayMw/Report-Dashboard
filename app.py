import io
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import streamlit as st
import pandas as pd
import plotly.express as px

from data_processing import load_raw, clean_data, prepare_raw_columns, NUMERIC_COLS
from report_generator import build_report, build_completion_report
from google_sheets import fetch_google_sheet
from column_mapping import (
    suggest_mapping, load_saved_mapping, save_mapping, apply_mapping, REQUIRED_FIELDS
)
from auth import verify_pin, set_pin, is_default_pin_active
from master_reference import (
    load_master, zone_center_matrix, completion_report,
    district_options, filter_by_district, districts_for_zones, districts_for_centers, normalize_name,
    find_unmatched_zones, apply_zone_assignments,
    find_unmatched_centers, apply_center_assignments,
)
from phases import (
    list_phases, get_active_phase, set_active_phase, create_phase,
    update_active_phase, update_phase, delete_phase, phase_dates, master_path_for,
    load_zone_assignments, save_zone_assignment,
    load_center_assignments, save_center_assignment,
)

# Activity dates/title/sheet/master all come from the active phase (phases.py)


def _default_report_range(phase_start, phase_end):
    """Default date range for the Trend and Reporting Status pickers:
    phase start through today, capped at the phase end — admins can still
    manually drag the picker up to the phase end / today, whichever is later.

    Before 4pm, today's reports aren't expected to be in yet, so the
    effective 'today' for default purposes rolls back to yesterday —
    otherwise today would show as an (unfairly) incomplete day.
    Uses Malawi local time explicitly, since the server itself almost
    certainly runs in UTC."""
    now = datetime.now(ZoneInfo("Africa/Blantyre"))
    effective_today = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
    default_end = min(effective_today, phase_end)
    if default_end < phase_start:
        default_end = phase_start
    max_bound = max(phase_end, now.date())
    return phase_start, default_end, max_bound

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
# Active phase (activity) — title, dates, sheet URL, master list all
# come from here; admins can create/switch phases without code changes.
# ------------------------------------------------------------------
phase = get_active_phase()
phase_start, phase_end = phase_dates(phase)
st.info(f"**Activity:** {phase['title']}  ({phase_start.strftime('%d %b %Y')} – {phase_end.strftime('%d %b %Y')})")

if is_admin:
    with st.expander("🗃️ Phases / Activities (admin)"):
        all_phases = list_phases()
        labels = {p["id"]: f"{p['title']} ({p['start_date']} – {p['end_date']})" for p in all_phases}
        ids = [p["id"] for p in all_phases]
        picked = st.selectbox(
            "Active phase", ids,
            index=ids.index(phase["id"]),
            format_func=lambda i: labels[i],
            key="phase_picker",
        )
        if picked != phase["id"] and st.button("Switch to selected phase"):
            set_active_phase(picked)
            st.rerun()

        sel_phase = next(p for p in all_phases if p["id"] == picked)

        st.divider()
        st.markdown("**Edit selected phase**")
        e_title = st.text_input("Title", value=sel_phase["title"], key=f"edit_title_{picked}")
        ec1, ec2 = st.columns(2)
        e_start = ec1.date_input("Start date", value=date.fromisoformat(sel_phase["start_date"]),
                                 key=f"edit_start_{picked}")
        e_end = ec2.date_input("End date", value=date.fromisoformat(sel_phase["end_date"]),
                               key=f"edit_end_{picked}")
        e_url = st.text_input("Google Sheet URL", value=sel_phase.get("google_sheet_url", ""),
                              key=f"edit_url_{picked}")
        e_master = st.file_uploader(
            "Replace master reference CSV (optional — columns: District, Zone, Center)",
            type=["csv"], key=f"edit_master_{picked}",
        )
        if st.button("Save changes", key=f"edit_save_{picked}"):
            if not e_title.strip():
                st.error("Title can't be empty.")
            elif e_end < e_start:
                st.error("End date must be on or after the start date.")
            else:
                e_master_bytes = None
                if e_master is not None:
                    e_master_bytes = e_master.getvalue()
                    try:
                        check = pd.read_csv(io.BytesIO(e_master_bytes), nrows=1)
                        missing_cols = [c for c in ("District", "Zone", "Center") if c not in check.columns]
                    except Exception:
                        missing_cols = ["District", "Zone", "Center"]
                    if missing_cols:
                        st.error(f"Master CSV is missing column(s): {', '.join(missing_cols)}.")
                        st.stop()
                update_phase(picked, title=e_title.strip(), start_date=e_start, end_date=e_end,
                             google_sheet_url=e_url.strip(), master_csv_bytes=e_master_bytes)
                st.success("Phase updated.")
                st.rerun()

        st.markdown("**Delete selected phase**")
        st.caption(
            "Deletes the phase, its name-correction assignments, and its own "
            "master CSV (if it uploaded one). The submitted data in its Google "
            "Sheet is NOT touched. This cannot be undone."
        )
        del_ok = st.checkbox(
            f"Yes, permanently delete '{sel_phase['title']}'", key=f"del_confirm_{picked}"
        )
        if st.button("Delete phase", key=f"del_btn_{picked}", disabled=not del_ok):
            ok, msg = delete_phase(picked)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

        st.divider()
        st.markdown("**Create a new phase**")
        st.caption(
            "A phase is one activity: its own dates, Google Sheet, master "
            "zone/center list, and name corrections. Leave the master CSV "
            "empty to reuse the current master list (same locations, new "
            "time period)."
        )
        new_title = st.text_input("Title", key="new_phase_title",
                                  placeholder="e.g. NID & NRBC Registration — Lilongwe, Oct 2026")
        c1, c2 = st.columns(2)
        new_start = c1.date_input("Start date", key="new_phase_start")
        new_end = c2.date_input("End date", key="new_phase_end")
        new_url = st.text_input("Google Sheet URL (optional, can be set later)", key="new_phase_url")
        new_master = st.file_uploader(
            "Master reference CSV (optional — columns: District, Zone, Center)",
            type=["csv"], key="new_phase_master",
        )
        if st.button("Create phase"):
            if not new_title.strip():
                st.error("Give the phase a title.")
            elif new_end < new_start:
                st.error("End date must be on or after the start date.")
            else:
                master_bytes = None
                if new_master is not None:
                    master_bytes = new_master.getvalue()
                    try:
                        check = pd.read_csv(io.BytesIO(master_bytes), nrows=1)
                        missing_cols = [c for c in ("District", "Zone", "Center") if c not in check.columns]
                    except Exception:
                        missing_cols = ["District", "Zone", "Center"]
                    if missing_cols:
                        st.error(f"Master CSV is missing column(s): {', '.join(missing_cols)}.")
                        st.stop()
                created = create_phase(new_title.strip(), new_start, new_end, new_url.strip(), master_bytes)
                set_active_phase(created["id"])
                st.success(f"Phase '{new_title}' created and activated.")
                st.rerun()

# ------------------------------------------------------------------
# Data source: Google Sheet (live) or manual upload
# ------------------------------------------------------------------
raw_df = None
saved_url = phase.get("google_sheet_url", "")

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
                update_active_phase(google_sheet_url=sheet_url)
            if refresh_clicked:
                st.session_state.sheet_refresh_token += 1

            @st.cache_data(ttl=900, show_spinner="Fetching latest responses from Google Sheets...")
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
    if saved_url:
        @st.cache_data(ttl=900, show_spinner="Loading latest data...")
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
# Column mapping — resilient to header drift (renamed/reordered columns)
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


# Cache the full cleaning + assignment pipeline. Streamlit reruns this whole
# script on every widget interaction (a filter change, a button click
# anywhere on the page) — without caching, ~1000 rows of cleaning, dedup,
# and date-correction would redo identical work dozens of times per minute
# even though the underlying data only changes once per fetch cycle.
@st.cache_data(show_spinner=False)
def _cached_pipeline(raw_df, zone_assignments, center_assignments, master_path, p_start, p_end):
    cleaned_df, report = clean_data(raw_df, p_start, p_end)
    master_df = load_master(master_path)
    cleaned_df = apply_zone_assignments(cleaned_df, zone_assignments)
    cleaned_df = apply_center_assignments(cleaned_df, center_assignments)
    return cleaned_df, report, master_df


zone_assignments = load_zone_assignments()
center_assignments = load_center_assignments()
cleaned_df, report, master_df = _cached_pipeline(
    raw_df, zone_assignments, center_assignments,
    master_path_for(phase), phase_start, phase_end,
)

# ------------------------------------------------------------------
# Data quality review panel
# ------------------------------------------------------------------
with st.expander("🔍 Data Quality Review", expanded=False):
    st.write(f"**Duplicate/resubmitted rows auto-removed (kept latest by timestamp):** {report['duplicates_removed_count']}")
    if len(report["duplicates_found"]):
        st.dataframe(report["duplicates_found"][["Timestamp", "ZONE NAME", "CENTER NAME", "Date"] + NUMERIC_COLS])

    DIAG_COLS = ["ZONE NAME", "CENTER NAME", "Date",
                 "Total Male Births Registered", "Total Female Births Registered",
                 "Total Males Processed", "Total Females Processed"]

    if len(report["missing_numeric_rows"]):
        st.warning(f"{len(report['missing_numeric_rows'])} row(s) had missing numeric values (filled with 0).")
        cols_present = [c for c in DIAG_COLS if c in report["missing_numeric_rows"].columns]
        st.dataframe(report["missing_numeric_rows"][cols_present].astype(str))

    if len(report["negative_rows"]):
        st.warning(f"{len(report['negative_rows'])} row(s) had negative numbers.")
        cols_present = [c for c in DIAG_COLS if c in report["negative_rows"].columns]
        st.dataframe(report["negative_rows"][cols_present].astype(str))

    if len(report["non_integer_rows"]):
        st.warning(
            f"{len(report['non_integer_rows'])} row(s) have a non-whole-number value "
            "(e.g. 0.01) in a count column — these are almost certainly data entry "
            "errors and are worth checking with the reporting center."
        )
        st.dataframe(report["non_integer_rows"][
            [c for c in DIAG_COLS if c in report["non_integer_rows"].columns]
        ])

    if len(report.get("out_of_range_rows", [])):
        st.warning(
            f"{len(report['out_of_range_rows'])} row(s) have a date outside the "
            f"activity window ({phase_start.strftime('%d %b')}–{phase_end.strftime('%d %b %Y')}) "
            "that couldn't be auto-corrected — likely data entry errors. They are "
            "excluded from all figures (the date filter is bounded to the activity "
            "window). Fix the date in the source sheet to include them."
        )
        st.dataframe(report["out_of_range_rows"].astype(str))

    if len(report["date_corrected_rows"]):
        st.info(
            f"{len(report['date_corrected_rows'])} row(s) had a date outside the "
            f"activity window ({phase_start.strftime('%d %b')}–{phase_end.strftime('%d %b')}) "
            "that resolved correctly once day/month were swapped — auto-corrected. "
            "This usually means a spreadsheet locale mismatch, not a data entry mistake."
        )
        st.dataframe(report["date_corrected_rows"])

    st.subheader("Zone Assignment")
    st.caption(
        "Submitted zone names that don't match any of the predefined zones "
        "from the official campaign registry. Assign each to the correct zone."
    )

    @st.cache_data(show_spinner=False)
    def _cached_unmatched_zones(cleaned_df, master_df):
        return find_unmatched_zones(cleaned_df, master_df)

    unmatched_zones = _cached_unmatched_zones(cleaned_df, master_df)
    if not unmatched_zones:
        st.write("All submitted zone names match the master reference. ✅")
    else:
        st.warning(f"{len(unmatched_zones)} submitted zone name(s) need assignment.")
        if is_admin:
            zone_options = sorted(master_df["Zone"].unique())
            for u in unmatched_zones:
                c1, c2, c3 = st.columns([2, 2, 1])
                c1.write(f"**'{u['zone']}'**")
                default_idx = zone_options.index(u["suggested_zone"]) if u["suggested_zone"] in zone_options else 0
                chosen = c2.selectbox("Correct zone", zone_options, index=default_idx,
                                       key=f"zoneassign_{u['zone']}", label_visibility="collapsed")
                if c3.button("Assign", key=f"zoneassign_btn_{u['zone']}"):
                    save_zone_assignment(u["zone"], chosen)
                    st.rerun()
        else:
            st.dataframe(pd.DataFrame(unmatched_zones))

    st.subheader("Center Assignment")
    st.caption(
        "Submitted center names that don't match any known center. If the "
        "zone is also valid, choices are scoped to that zone's centers; "
        "otherwise you can pick from the full list — independent of whether "
        "Zone Assignment above has been resolved."
    )

    @st.cache_data(show_spinner=False)
    def _cached_unmatched_centers(cleaned_df, master_df):
        return find_unmatched_centers(cleaned_df, master_df)

    unmatched_centers = _cached_unmatched_centers(cleaned_df, master_df)
    if not unmatched_centers:
        st.write("All submitted center names match the master reference. ✅")
    else:
        st.warning(f"{len(unmatched_centers)} submitted center name(s) need assignment.")
        if is_admin:
            all_centers_sorted = sorted(master_df["Center"].unique())
            for u in unmatched_centers:
                zone_matches = master_df["Zone"].str.upper() == u["zone"].strip().upper()
                center_options = sorted(master_df.loc[zone_matches, "Center"].unique())
                if not center_options:
                    center_options = all_centers_sorted  # zone itself doesn't match — pick from everything
                c1, c2, c3 = st.columns([2, 2, 1])
                c1.write(f"**'{u['zone']}' / '{u['center']}'**")
                default_idx = center_options.index(u["suggested_center"]) if u["suggested_center"] in center_options else 0
                chosen = c2.selectbox("Correct center", center_options, index=default_idx,
                                       key=f"centerassign_{u['zone']}_{u['center']}", label_visibility="collapsed")
                if c3.button("Assign", key=f"centerassign_btn_{u['zone']}_{u['center']}"):
                    save_center_assignment(u["zone"], u["center"], chosen)
                    st.rerun()
        else:
            st.dataframe(pd.DataFrame(unmatched_centers))

# Everything below only counts rows mapped to the master list (the sidebar
# filters are master-driven), so surface how much data is being held back —
# otherwise unmapped submissions would vanish from the totals silently.
_n_unmapped = int((~(
    cleaned_df["ZONE NAME"].apply(normalize_name).isin(set(master_df["Zone_norm"]))
    & cleaned_df["CENTER NAME"].apply(normalize_name).isin(set(master_df["Center_norm"]))
)).sum())
if _n_unmapped:
    st.warning(
        f"{_n_unmapped} submitted row(s) have a zone or center not yet mapped "
        "to the master reference list and are excluded from all figures below. "
        "Resolve them in Zone/Center Assignment (Data Quality Review) to include them."
    )

# ------------------------------------------------------------------
# District scope — everything below (sidebar filters, KPIs, charts,
# reporting status, exports) sees only the selected district's data.
# A submitted row's district comes from its (assignment-corrected) zone
# looked up in the master list; rows whose zone is still unmapped can't
# be placed in a district, so they only appear under ALL.
# ------------------------------------------------------------------
district_choice = st.radio(
    "District",
    ["ALL"] + district_options(master_df),
    horizontal=True,
    key="district_scope",
)
if district_choice != "ALL":
    cleaned_df, master_df = filter_by_district(cleaned_df, master_df, district_choice)
    if cleaned_df.empty:
        st.warning(f"No submissions mapped to {district_choice} yet.")
        st.stop()

# ------------------------------------------------------------------
# Sidebar filters
# ------------------------------------------------------------------
st.sidebar.header("Filters")
# Zone/Center options come from the master reference list (already scoped by
# the district toggle) — NOT from submitted data — so a center that hasn't
# reported at all is still selectable and visible downstream. Submitted rows
# are matched on normalized names; rows whose zone or center isn't mapped to
# the master list are excluded from these views entirely (resolve them in
# Zone/Center Assignment above to bring them in).
zones = sorted(master_df["Zone"].unique())
selected_zones = st.sidebar.multiselect("Zone", zones, default=zones)
selected_zone_norms = {normalize_name(z) for z in selected_zones}

centers_available = sorted(master_df.loc[master_df["Zone_norm"].isin(selected_zone_norms), "Center"].unique())
selected_centers = st.sidebar.multiselect("Center", centers_available, default=centers_available)
selected_center_norms = {normalize_name(c) for c in selected_centers}

# The date filter is bounded by the ACTIVE PHASE's window — never by the
# data. A mistyped date (e.g. 8 Sep in a July campaign) used to stretch
# this picker months wide; such rows are now flagged in Data Quality
# Review instead and excluded by these bounds.
min_date = phase_start
max_date = phase_end
date_range = st.sidebar.date_input("Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = min_date, max_date

zc_mask = (
    cleaned_df["ZONE NAME"].apply(normalize_name).isin(selected_zone_norms)
    & cleaned_df["CENTER NAME"].apply(normalize_name).isin(selected_center_norms)
)

filtered = cleaned_df[
    zc_mask
    & (cleaned_df["Date"].dt.date >= start_date)
    & (cleaned_df["Date"].dt.date <= end_date)
].copy()

# Zone/Center filtered but NOT date-filtered — for tabs (Trend) that use
# their own dedicated date range picker instead of the sidebar's.
filtered_zone_center_only = cleaned_df[zc_mask].copy()

# Standardize display casing to the master list's spelling, so one center
# submitted under two casings can't split into two rows in groupbys/charts.
_zone_disp = dict(zip(master_df["Zone_norm"], master_df["Zone"]))
_center_disp = dict(zip(master_df["Center_norm"], master_df["Center"]))
for _df in (filtered, filtered_zone_center_only):
    _df["ZONE NAME"] = _df["ZONE NAME"].apply(lambda z: _zone_disp[normalize_name(z)])
    _df["CENTER NAME"] = _df["CENTER NAME"].apply(lambda c: _center_disp[normalize_name(c)])

if filtered.empty:
    st.warning("No data matches the current filters.")
    st.stop()

# ------------------------------------------------------------------
# KPIs
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
)
zone_summary.columns = [
    "Total Birth Registrations", "Male Births", "Female Births",
    "Total NID Registrations", "Male NID", "Female NID",
]
# Include every selected master-list zone, even ones with no submissions yet —
# a zone that hasn't reported shows up with zeros instead of vanishing.
zone_summary = (
    zone_summary.reindex(sorted(selected_zones))
    .fillna(0).astype(int)
    .sort_values("Total Birth Registrations", ascending=False)
)
# Under ALL, zones from both districts are mixed in one table — label each
# with its district so the view stays readable. (Redundant when a single
# district is selected, so skipped there.)
if district_choice == "ALL":
    zone_summary.insert(0, "District", districts_for_zones(zone_summary.index, master_df))

center_summary = filtered.groupby("CENTER NAME").agg(
    Total_Birth_Registrations=("Total Birth Registrations", "sum"),
    Male_Births=("Total Male Births Registered", "sum"),
    Female_Births=("Total Female Births Registered", "sum"),
    Total_NID_Registrations=("Total NID Registrations", "sum"),
    Male_NID=("Total Males Processed", "sum"),
    Female_NID=("Total Females Processed", "sum"),
)
center_summary.columns = [
    "Total Birth Registrations", "Male Births", "Female Births",
    "Total NID Registrations", "Male NID", "Female NID",
]
# Same as zone_summary: every selected master-list center appears, zeros
# for centers that haven't reported.
center_summary = (
    center_summary.reindex(sorted(set(selected_centers)))
    .fillna(0).astype(int)
    .sort_values("Total Birth Registrations", ascending=False)
)

# ------------------------------------------------------------------
# Charts
# ------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["By Zone", "By Center", "Trend Over Time", "Male vs Female", "Reporting Status"]
)

with tab1:
    fig = px.bar(zone_summary.reset_index(), x="ZONE NAME", y="Total Birth Registrations",
                 title="Birth Registrations by Zone", text_auto=True)
    st.plotly_chart(fig, width='stretch')

    nid_sorted = zone_summary.reset_index().sort_values("Total NID Registrations", ascending=False)
    fig_nid = px.bar(nid_sorted, x="ZONE NAME", y="Total NID Registrations",
                      title="NID Registrations by Zone", text_auto=True)
    st.plotly_chart(fig_nid, width='stretch')

    st.dataframe(zone_summary)

with tab2:
    top_n = st.slider("Show top N centers", 5, min(50, len(center_summary)), min(15, len(center_summary)))
    top_centers = center_summary.head(top_n)
    fig = px.bar(top_centers.reset_index(), x="CENTER NAME", y="Total Birth Registrations",
                 title=f"Birth Registrations — Top {top_n} Centers", text_auto=True)
    fig.update_xaxes(tickangle=45)
    st.plotly_chart(fig, width='stretch')

    nid_top_sorted = center_summary.reset_index().sort_values("Total NID Registrations", ascending=False).head(top_n)
    fig_nid = px.bar(nid_top_sorted, x="CENTER NAME", y="Total NID Registrations",
                      title=f"NID Registrations — Top {top_n} Centers", text_auto=True)
    fig_nid.update_xaxes(tickangle=45)
    st.plotly_chart(fig_nid, width='stretch')

    st.dataframe(center_summary)

with tab3:
    default_start_3, default_end_3, max_bound_3 = _default_report_range(phase_start, phase_end)
    range_3 = st.date_input(
        "Date range", value=(default_start_3, default_end_3),
        min_value=phase_start, max_value=max_bound_3,
        key="trend_range",
    )
    if isinstance(range_3, tuple) and len(range_3) == 2:
        start_3, end_3 = range_3
    else:
        start_3, end_3 = default_start_3, default_end_3

    trend_source = filtered_zone_center_only[
        (filtered_zone_center_only["Date"].dt.date >= start_3)
        & (filtered_zone_center_only["Date"].dt.date <= end_3)
    ]
    trend = trend_source.groupby(trend_source["Date"].dt.date).agg(
        Total_Birth_Registrations=("Total Birth Registrations", "sum"),
        Total_NID_Registrations=("Total NID Registrations", "sum"),
    ).reset_index()
    trend.columns = ["Date", "Total Birth Registrations", "Total NID Registrations"]
    trend = trend.sort_values("Date")
    if len(trend) > 1:
        fig = px.line(trend, x="Date", y=["Total Birth Registrations", "Total NID Registrations"], markers=True,
                      title="Birth & NID Registrations Over Time")
        # Same month throughout the activity — show day-of-month, not month names,
        # and force one tick per actual day so it can't be misread as spanning months.
        fig.update_xaxes(tickformat="%d %b", dtick="D1")
        st.plotly_chart(fig, width='stretch')
    else:
        st.info("Only one date in the selected range — trend chart will populate as more days are added.")
    st.dataframe(trend)

with tab4:
    st.markdown("**Birth Registration — Male vs Female**")
    zone_mf_birth = filtered.groupby("ZONE NAME").agg(
        Male=("Total Male Births Registered", "sum"),
        Female=("Total Female Births Registered", "sum"),
    ).reset_index()
    zone_mf_birth["Total"] = zone_mf_birth["Male"] + zone_mf_birth["Female"]
    zone_mf_birth = zone_mf_birth.sort_values("Total", ascending=False)
    fig = px.bar(zone_mf_birth, x="ZONE NAME", y=["Male", "Female"], barmode="stack",
                 title="Birth Registrations by Zone — Male vs Female")
    st.plotly_chart(fig, width='stretch')

    overall_birth = pd.DataFrame({"Sex": ["Male", "Female"], "Count": [total_male_birth, total_female_birth]})
    fig_pie = px.pie(overall_birth, names="Sex", values="Count", title="Overall Birth Registration Male/Female Split")
    st.plotly_chart(fig_pie, width='stretch')

    st.markdown("**NID Registration — Male vs Female**")
    zone_mf_nid = filtered.groupby("ZONE NAME").agg(
        Male=("Total Males Processed", "sum"),
        Female=("Total Females Processed", "sum"),
    ).reset_index()
    zone_mf_nid["Total"] = zone_mf_nid["Male"] + zone_mf_nid["Female"]
    zone_mf_nid = zone_mf_nid.sort_values("Total", ascending=False)
    fig2 = px.bar(zone_mf_nid, x="ZONE NAME", y=["Male", "Female"], barmode="stack",
                  title="NID Registrations by Zone — Male vs Female")
    st.plotly_chart(fig2, width='stretch')

    overall_nid = pd.DataFrame({"Sex": ["Male", "Female"], "Count": [total_male_nid, total_female_nid]})
    fig_pie2 = px.pie(overall_nid, names="Sex", values="Count", title="Overall NID Registration Male/Female Split")
    st.plotly_chart(fig_pie2, width='stretch')

with tab5:
    st.caption(
        f"Activity runs {phase_start.strftime('%d %b')} to {phase_end.strftime('%d %b %Y')}. "
        "Pick a zone and date range to see each center's submissions."
    )
    default_start_5, default_end_5, max_bound_5 = _default_report_range(phase_start, phase_end)
    range_5 = st.date_input(
        "Date range", value=(default_start_5, default_end_5),
        min_value=phase_start, max_value=max_bound_5,
        key="reporting_status_range",
    )
    if isinstance(range_5, tuple) and len(range_5) == 2:
        start_5, end_5 = range_5
    else:
        start_5, end_5 = default_start_5, default_end_5

    zone_options_5 = sorted(master_df["Zone"].unique())
    picked_zone = st.selectbox("Zone", zone_options_5, key="reporting_status_zone")

    matrix = zone_center_matrix(cleaned_df, master_df, picked_zone, start_5, end_5)
    day_cols = [c for c in matrix.columns if c != "Center"]
    total_expected = len(matrix) * len(day_cols)
    total_submitted = int((matrix[day_cols] > 0).sum().sum())
    pct = round(100 * total_submitted / total_expected, 1) if total_expected else 0
    st.metric(f"Reports submitted in {picked_zone}", f"{total_submitted} / {total_expected}  :  {pct}%")
    st.dataframe(
        matrix,
        hide_index=True,
        column_config={
            c: st.column_config.NumberColumn(c, format="%d") for c in day_cols
        },
    )

    st.divider()
    st.caption(
        "Exports completion for every predefined zone and center over the date "
        "range above. Only counts submissions already mapped to a predefined "
        "zone/center (via the Zone/Center Assignment above) — unmapped "
        "submissions are excluded, not guessed at."
    )
    if st.button("📊 Generate Zone & Center Completion Report"):
        district_completion, zone_completion, center_completion = completion_report(
            cleaned_df, master_df, start_5, end_5
        )
        st.session_state.completion_report_bytes = build_completion_report(
            district_completion, zone_completion, center_completion, start_5, end_5
        )

    if st.session_state.get("completion_report_bytes"):
        st.download_button(
            "Download Completion Report",
            data=st.session_state.completion_report_bytes,
            file_name="zone_center_completion_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

st.divider()

# ------------------------------------------------------------------
# Export — admin only
# ------------------------------------------------------------------
if is_admin:
    st.subheader("📤 Export Report (admin)")
    st.caption("Report generation only runs when you click below — it doesn't rebuild on every page interaction.")
    if st.button("Generate Excel Report"):
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
            "District": district_choice,
            "Activity": phase["title"],
        }
        # The export always carries district labels — even under ALL — so the
        # workbook can separate each district into its own section/charts.
        zone_export = zone_summary.copy()
        if "District" not in zone_export.columns:
            zone_export.insert(0, "District", districts_for_zones(zone_export.index, master_df))
        center_export = center_summary.copy()
        if "District" not in center_export.columns:
            center_export.insert(0, "District", districts_for_centers(center_export.index, master_df))
        filtered_export = filtered.copy()
        filtered_export["District"] = districts_for_zones(filtered_export["ZONE NAME"], master_df)
        st.session_state.excel_report_bytes = build_report(filtered_export, report, zone_export, center_export, kpis)

    if st.session_state.get("excel_report_bytes"):
        st.download_button(
            "Download Excel Report",
            data=st.session_state.excel_report_bytes,
            file_name="nrbc_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )