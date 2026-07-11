"""
Data cleaning and processing for NRBC daily reporting form data.
"""
import re
import difflib
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


def clean_data(df: pd.DataFrame, confirmed_merges: dict = None):
    """
    Clean the raw dataframe.

    confirmed_merges: optional dict like {"zone": {"Bqengu": "Bwengu"}, "center": {...}}
    used to apply user-confirmed fuzzy-match merges on top of automatic whitespace/case cleanup.

    Returns:
        cleaned_df: the cleaned dataframe
        report: dict with data-quality findings
    """
    confirmed_merges = confirmed_merges or {"zone": {}, "center": {}}
    report = {}
    df = df.copy()

    # --- 1. Parse dates ---
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

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
        # for each key, find most common surface form
        mapping = {}
        for k, grp in series.groupby(key):
            mapping[k] = grp.value_counts().idxmax()
        return key.map(mapping), key

    df["ZONE NAME"], zone_key = canonicalize(df["ZONE NAME"])
    df["CENTER NAME"], center_key = canonicalize(df["CENTER NAME"])

    # --- 3. Apply confirmed fuzzy merges (user-approved typo corrections) ---
    for wrong, right in confirmed_merges.get("zone", {}).items():
        df.loc[df["ZONE NAME"].str.upper() == wrong.upper(), "ZONE NAME"] = right
    for wrong, right in confirmed_merges.get("center", {}).items():
        df.loc[df["CENTER NAME"].str.upper() == wrong.upper(), "CENTER NAME"] = right

    # --- 4. Numeric coercion ---
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

    # --- 5. Exact duplicates: same zone/center/date/numbers -> keep latest by Timestamp ---
    dup_subset = ["ZONE NAME", "CENTER NAME", "Date"] + NUMERIC_COLS
    is_dup = df.duplicated(subset=dup_subset, keep=False)
    dup_rows = df[is_dup].sort_values(["ZONE NAME", "CENTER NAME", "Timestamp"])
    report["duplicates_found"] = dup_rows

    df = df.sort_values("Timestamp")
    df_deduped = df.drop_duplicates(subset=dup_subset, keep="last")
    report["duplicates_removed_count"] = len(df) - len(df_deduped)
    df = df_deduped

    # --- 6. Derived columns ---
    # Note: Birth Registration and NID Registration are two SEPARATE activities
    # captured on the same daily form (not a before/after pipeline), so we track
    # them as independent totals rather than computing a "processing rate".
    df["Total Birth Registrations"] = df["Total Male Births Registered"] + df["Total Female Births Registered"]
    df["Total NID Registrations"] = df["Total Males Processed"] + df["Total Females Processed"]

    df = df.sort_values("Date").reset_index(drop=True)

    return df, report


def find_fuzzy_suggestions(df: pd.DataFrame, column: str, cutoff: float = 0.75):
    """
    Suggest possible typo groupings within a column (e.g. Zone or Center names)
    that are NOT already identical after whitespace/case normalization.
    Returns a list of dicts: {"a": name1, "b": name2, "score": similarity}
    """
    names = sorted(df[column].dropna().unique().tolist())
    suggestions = []
    seen_pairs = set()
    for i, name in enumerate(names):
        matches = difflib.get_close_matches(name, names[i + 1:], n=3, cutoff=cutoff)
        for m in matches:
            pair = tuple(sorted([name, m]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            score = difflib.SequenceMatcher(None, name, m).ratio()
            suggestions.append({"a": pair[0], "b": pair[1], "score": round(score, 3)})
    suggestions.sort(key=lambda x: -x["score"])
    return suggestions
