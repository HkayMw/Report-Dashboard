"""
Data cleaning and processing for NRBC daily reporting form data.
"""
import re
from datetime import date
import pandas as pd
import numpy as np

RAW_COLUMNS = [
    "Timestamp", "ZONE NAME", "CENTER NAME", "Date",
    "Total Male Births Registered", "Total Female Births Registered",
    "Total Males Processed", "Total Females Processed",
]

NUMERIC_COLS = [
    "Total Male Births Registered", "Total Female Births Registered",
    "Total Males Processed", "Total Females Processed",
]

# The activity's fixed date range — single source of truth, also used by
# app.py's Reporting Status tab and by the date-correction heuristic below.
# Update here if the campaign's dates change.
ACTIVITY_START_DATE = date(2026, 7, 8)
ACTIVITY_END_DATE = date(2026, 7, 18)


def _fix_swapped_day_month(dt_series: pd.Series, valid_start: date, valid_end: date) -> pd.Series:
    """
    If a parsed date falls outside [valid_start, valid_end], but swapping its
    day and month would bring it inside that range, assume the day/month got
    transposed upstream — e.g. Google Sheets auto-converting a DD/MM-typed
    date using a US MM/DD locale — and correct it. Leaves the time-of-day
    component untouched. Dates that don't fall into range either way are left
    as-is (they'll surface via the existing bad-date / out-of-range checks).
    """
    def fix_one(ts):
        if pd.isna(ts):
            return ts
        d = ts.date()
        if valid_start <= d <= valid_end:
            return ts
        try:
            swapped = ts.replace(day=ts.month, month=ts.day)
        except ValueError:
            return ts  # e.g. original day > 12, can't be a valid month
        if valid_start <= swapped.date() <= valid_end:
            return swapped
        return ts
    return dt_series.apply(fix_one)


def normalize_text(s):
    """Trim, collapse internal whitespace. Keeps original casing intact
    except we standardize to Title Case for display consistency."""
    if pd.isna(s):
        return s
    s = str(s).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def prepare_raw_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop fully-empty trailing columns. Column selection/renaming to canonical
    fields now happens via column_mapping.apply_mapping() before this is called
    downstream, or this can be used standalone for the legacy exact-name path."""
    df = df.dropna(axis=1, how="all")
    return df


def load_raw(file) -> pd.DataFrame:
    """Load an uploaded Excel file and prep its columns."""
    df = pd.read_excel(file)
    return prepare_raw_columns(df)


def clean_data(df: pd.DataFrame):
    """
    Clean the raw dataframe: parse dates, normalize zone/center text casing,
    coerce numeric fields, flag data-quality issues, dedupe.

    Zone/center CORRECTION (typo fixes, zone/center swaps, mapping to the
    predefined master list) is handled separately by master_reference.py's
    apply_assignments(), applied by the caller after this function returns —
    that keeps generic cleaning here decoupled from the campaign-specific
    master reference list.

    Returns:
        cleaned_df: the cleaned dataframe
        report: dict with data-quality findings
    """
    report = {}
    df = df.copy()

    # --- 1. Parse dates ---
    # dayfirst=True: this data uses DD/MM/YYYY (Malawi convention). Without this,
    # pandas' per-value format guessing silently misreads e.g. "08/07/2026"
    # (8th July) as month=08 day=07 (7th August) for any day-of-month <= 12,
    # scattering rows across the wrong months.
    # Google Sheets' CSV export gives dates in unambiguous ISO format
    # (YYYY-MM-DD), so no dayfirst disambiguation is needed — in fact
    # dayfirst=True actively misparses ISO strings when the day is <=12
    # (a genuine pandas quirk), so it's deliberately NOT used here. Any
    # day/month corruption that happens upstream of this (e.g. a spreadsheet
    # locale mismatch) is instead caught by the swap-correction heuristic below.
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    # Catch dates that arrived already-corrupted upstream (e.g. a spreadsheet
    # locale mismatch), which dayfirst=True can't fix since ISO-formatted
    # strings have no ambiguity left to resolve at parse time.
    pre_fix_date = df["Date"].copy()
    df["Date"] = _fix_swapped_day_month(df["Date"], ACTIVITY_START_DATE, ACTIVITY_END_DATE)
    df["Timestamp"] = _fix_swapped_day_month(df["Timestamp"], ACTIVITY_START_DATE, ACTIVITY_END_DATE)
    date_corrected_mask = (df["Date"] != pre_fix_date) & df["Date"].notna() & pre_fix_date.notna()
    report["date_corrected_rows"] = df.loc[
        date_corrected_mask, ["ZONE NAME", "CENTER NAME"]
    ].assign(**{"Original Date": pre_fix_date[date_corrected_mask].dt.date, "Corrected Date": df.loc[date_corrected_mask, "Date"].dt.date})

    bad_dates = df[df["Date"].isna() | df["Timestamp"].isna()]
    report["bad_dates"] = bad_dates

    # --- 2. Normalize text fields (whitespace/case) ---
    df["ZONE NAME_raw"] = df["ZONE NAME"]
    df["CENTER NAME_raw"] = df["CENTER NAME"]

    df["ZONE NAME"] = df["ZONE NAME"].apply(normalize_text)
    df["CENTER NAME"] = df["CENTER NAME"].apply(normalize_text)

    # Canonicalize case-insensitively: group by uppercase key, pick the most
    # frequent original casing as the display form
    def canonicalize(series):
        key = series.str.upper()
        mapping = {}
        for k, grp in series.groupby(key):
            mapping[k] = grp.value_counts().idxmax()
        return key.map(mapping)

    df["ZONE NAME"] = canonicalize(df["ZONE NAME"])
    df["CENTER NAME"] = canonicalize(df["CENTER NAME"])

    # --- 3. Numeric coercion ---
    numeric_issues = {}
    for col in NUMERIC_COLS:
        original = df[col]
        coerced = pd.to_numeric(original, errors="coerce")
        bad_mask = coerced.isna() & original.notna()
        if bad_mask.any():
            numeric_issues[col] = df.loc[bad_mask, ["ZONE NAME", "CENTER NAME", "Date", col]]
        df[col] = coerced
    report["numeric_issues"] = numeric_issues

    # Negative values flag
    neg_mask = (df[NUMERIC_COLS] < 0).any(axis=1)
    report["negative_rows"] = df[neg_mask]

    # Non-integer values flag - these columns are counts of people, so any
    # fractional value (e.g. 0.01) is very likely a data entry error, not
    # a real observation
    def _has_fraction(row):
        return any(pd.notna(row[c]) and float(row[c]) != int(row[c]) for c in NUMERIC_COLS)
    non_integer_mask = df.apply(_has_fraction, axis=1)
    report["non_integer_rows"] = df[non_integer_mask]

    # Missing numeric values -> fill with 0 for aggregation but track them
    missing_numeric_mask = df[NUMERIC_COLS].isna().any(axis=1)
    report["missing_numeric_rows"] = df[missing_numeric_mask]
    df[NUMERIC_COLS] = df[NUMERIC_COLS].fillna(0)

    # --- 4. Exact duplicates: same zone/center/date/numbers -> keep latest by Timestamp ---
    dup_subset = ["ZONE NAME", "CENTER NAME", "Date"] + NUMERIC_COLS
    is_dup = df.duplicated(subset=dup_subset, keep=False)
    dup_rows = df[is_dup].sort_values(["ZONE NAME", "CENTER NAME", "Timestamp"])
    report["duplicates_found"] = dup_rows

    df = df.sort_values("Timestamp")
    df_deduped = df.drop_duplicates(subset=dup_subset, keep="last")
    report["duplicates_removed_count"] = len(df) - len(df_deduped)
    df = df_deduped

    # --- 5. Derived columns ---
    # Note: Birth Registration and NID Registration are two SEPARATE activities
    # captured on the same daily form (not a before/after pipeline), so we track
    # them as independent totals rather than computing a "processing rate".
    df["Total Birth Registrations"] = df["Total Male Births Registered"] + df["Total Female Births Registered"]
    df["Total NID Registrations"] = df["Total Males Processed"] + df["Total Females Processed"]

    df = df.sort_values("Date").reset_index(drop=True)

    return df, report
