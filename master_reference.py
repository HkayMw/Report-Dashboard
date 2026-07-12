"""
Loads the authoritative master list of zones and centers (from the campaign's
official school/center registry PDFs) and provides:
  - authoritative typo/mismatch detection for submitted zone/center names
  - a persisted admin-assignment mechanism so every submission ends up mapped
    to a predefined (zone, center) pair
  - a lightweight per-zone submission matrix (center x day)

This is a static reference bundled with the app (master_reference.csv).
Assignments persist in config.json (flat file — plenty for a few hundred
entries at most, no need for a database at this scale).
"""
import os
import re
import json
import difflib
import pandas as pd

REFERENCE_PATH = os.path.join(os.path.dirname(__file__), "master_reference.csv")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def _normalize(s: str) -> str:
    s = str(s).strip().upper()
    s = re.sub(r"\s+", " ", s)
    return s


# Common suffixes PEAs tack onto a center's real name when filling the form
# (e.g. "CHIBAVI PRIMARY SCHOOL" for a master-listed center just called "CHIBAVI").
_SUFFIX_NOISE_PATTERN = re.compile(r"\s+(PRIMARY SCHOOL|SCHOOL|PRIMARY|P/S|P\.S\.?|PS)$")


def _strip_suffix_noise(s_norm: str) -> str:
    prev, cur = None, s_norm
    while prev != cur:
        prev = cur
        cur = _SUFFIX_NOISE_PATTERN.sub("", cur).strip()
    return cur


def load_master() -> pd.DataFrame:
    df = pd.read_csv(REFERENCE_PATH)
    df["Zone_norm"] = df["Zone"].apply(_normalize)
    df["Center_norm"] = df["Center"].apply(_normalize)
    return df


def master_zone_set(master_df: pd.DataFrame) -> set:
    return set(master_df["Zone_norm"])


def master_center_set(master_df: pd.DataFrame) -> set:
    return set(master_df["Center_norm"])


def master_pairs_set(master_df: pd.DataFrame) -> set:
    return set(zip(master_df["Zone_norm"], master_df["Center_norm"]))


def centers_by_zone(master_df: pd.DataFrame) -> dict:
    d = {}
    for zone_norm, grp in master_df.groupby("Zone_norm"):
        d[zone_norm] = set(grp["Center_norm"])
    return d


def zone_for_center(master_df: pd.DataFrame) -> dict:
    d = {}
    for _, row in master_df.iterrows():
        d.setdefault(row["Center_norm"], []).append(row["Zone_norm"])
    return d


# ---------------------------------------------------------------------------
# Assignment persistence (flat file, survives app restarts)
# ---------------------------------------------------------------------------

def load_assignments() -> list:
    """List of {"raw_zone", "raw_center", "zone", "center"} dicts."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f).get("pair_assignments", [])
        except Exception:
            return []
    return []


def save_assignment(raw_zone: str, raw_center: str, assigned_zone: str, assigned_center: str) -> None:
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {}
    assignments = data.get("pair_assignments", [])
    # replace any existing assignment for the same raw pair
    assignments = [
        a for a in assignments
        if not (_normalize(a["raw_zone"]) == _normalize(raw_zone) and _normalize(a["raw_center"]) == _normalize(raw_center))
    ]
    assignments.append({
        "raw_zone": raw_zone, "raw_center": raw_center,
        "zone": assigned_zone, "center": assigned_center,
    })
    data["pair_assignments"] = assignments
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def apply_assignments(df: pd.DataFrame, assignments: list) -> pd.DataFrame:
    """Overwrite ZONE NAME / CENTER NAME for rows matching a saved assignment."""
    if not assignments:
        return df
    df = df.copy()
    lookup = {
        (_normalize(a["raw_zone"]), _normalize(a["raw_center"])): (a["zone"], a["center"])
        for a in assignments
    }
    keys = list(zip(df["ZONE NAME"].apply(_normalize), df["CENTER NAME"].apply(_normalize)))
    new_zone = df["ZONE NAME"].tolist()
    new_center = df["CENTER NAME"].tolist()
    for i, k in enumerate(keys):
        if k in lookup:
            new_zone[i], new_center[i] = lookup[k]
    df["ZONE NAME"] = new_zone
    df["CENTER NAME"] = new_center
    return df


# ---------------------------------------------------------------------------
# Validation against the master list
# ---------------------------------------------------------------------------

def find_unassigned(cleaned_df: pd.DataFrame, master_df: pd.DataFrame) -> list:
    """
    Unique submitted (zone, center) pairs that don't match the master list
    (assignments should already have been applied to cleaned_df before calling
    this, so anything still unmatched here genuinely needs admin attention).

    Returns a list of dicts: zone, center, issue, suggestion, suggested_zone,
    suggested_center (best-guess defaults for the assignment dropdowns, may be None).
    """
    zones = master_zone_set(master_df)
    centers = master_center_set(master_df)
    pairs = master_pairs_set(master_df)
    by_zone = centers_by_zone(master_df)
    zone_lookup = zone_for_center(master_df)
    zone_display = {row["Zone_norm"]: row["Zone"] for _, row in master_df.iterrows()}
    center_display = {(row["Zone_norm"], row["Center_norm"]): row["Center"] for _, row in master_df.iterrows()}

    issues = []
    submitted_pairs = cleaned_df[["ZONE NAME", "CENTER NAME"]].drop_duplicates()

    for _, row in submitted_pairs.iterrows():
        zone, center = row["ZONE NAME"], row["CENTER NAME"]
        nz, nc = _normalize(zone), _normalize(center)

        if (nz, nc) in pairs:
            continue

        suggested_zone, suggested_center, issue, suggestion = None, None, "unknown", \
            f"'{zone}' / '{center}' doesn't match anything in the master reference."

        if nz not in zones:
            if nz in centers:
                actual_zones = zone_lookup.get(nz, [])
                if actual_zones:
                    suggested_zone = zone_display[actual_zones[0]]
                    suggested_center = zone
                issue = "zone_center_swap"
                suggestion = f"'{zone}' is listed as a CENTER (under zone '{suggested_zone}'), not a zone."
            else:
                m = difflib.get_close_matches(nz, zones, n=1, cutoff=0.6)
                if m:
                    suggested_zone = zone_display[m[0]]
                    issue = "zone_typo"
                    suggestion = f"'{zone}' doesn't match any known zone. Closest: '{suggested_zone}'."
        else:
            stripped_nc = _strip_suffix_noise(nc)
            if nc in zones:
                issue, suggestion = "wrong_zone_for_center", f"'{center}' is itself a zone name, not a center under '{zone}'."
            elif zone_lookup.get(nc):
                other_zone = zone_lookup[nc][0]
                suggested_zone = zone_display[other_zone]
                suggested_center = center_display.get((other_zone, nc), center)
                issue = "wrong_zone_for_center"
                suggestion = f"'{center}' exists under zone '{suggested_zone}', not '{zone}'."
            elif stripped_nc != nc and stripped_nc in by_zone.get(nz, set()):
                suggested_zone = zone
                suggested_center = center_display.get((nz, stripped_nc), center)
                issue = "center_typo"
                suggestion = f"'{center}' matches '{suggested_center}' after removing a common suffix."
            else:
                m = difflib.get_close_matches(nc, by_zone.get(nz, set()), n=1, cutoff=0.6)
                if m:
                    suggested_zone = zone
                    suggested_center = center_display.get((nz, m[0]), center)
                    issue = "center_typo"
                    suggestion = f"'{center}' doesn't match any known center under '{zone}'. Closest: '{suggested_center}'."

        issues.append({
            "zone": zone, "center": center, "issue": issue, "suggestion": suggestion,
            "suggested_zone": suggested_zone, "suggested_center": suggested_center,
        })

    return issues


# ---------------------------------------------------------------------------
# Zone-scoped submission matrix (center x day) — deliberately scoped to one
# zone at a time (rather than all 333 centers) to keep this cheap: a handful
# of centers by a fixed ~11-day window is a small pivot, not a heavy computation.
# ---------------------------------------------------------------------------

def zone_center_matrix(cleaned_df: pd.DataFrame, master_df: pd.DataFrame, zone: str, start_date, end_date):
    """
    For one zone, returns a DataFrame: rows = centers under that zone (from the
    master list), columns = each date in [start_date, end_date], values = number
    of submissions that center made that day (0 = missing).
    """
    nz = _normalize(zone)
    zone_centers = master_df.loc[master_df["Zone_norm"] == nz, ["Center", "Center_norm"]].drop_duplicates()

    sub = cleaned_df[cleaned_df["ZONE NAME"].apply(_normalize) == nz].copy()
    sub["_center_norm"] = sub["CENTER NAME"].apply(_normalize)
    sub["_date"] = sub["Date"].dt.date
    sub = sub[(sub["_date"] >= start_date) & (sub["_date"] <= end_date)]

    date_cols = list(pd.date_range(start_date, end_date, freq="D").date)

    counts = sub.groupby(["_center_norm", "_date"]).size().unstack(fill_value=0)
    counts = counts.reindex(columns=date_cols, fill_value=0)

    result = zone_centers.set_index("Center_norm").join(counts, how="left").fillna(0)
    result = result.rename(columns={c: c.strftime("%d %b") for c in date_cols})
    result = result.rename(columns={"Center": "Center"}).reset_index(drop=True)
    for c in result.columns:
        if c != "Center":
            result[c] = result[c].astype(int)
    return result.sort_values("Center").reset_index(drop=True)
