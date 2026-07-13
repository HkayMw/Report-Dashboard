"""
Loads the authoritative master list of zones and centers (from the campaign's
official school/center registry PDFs) and provides:
  - separate zone-name and center-name validation against that master list
  - two independent, persisted admin-assignment mechanisms (zone and center
    kept separate — usually only one of the two is actually wrong, so fixing
    them independently is simpler than picking both at once)
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


def centers_by_zone(master_df: pd.DataFrame) -> dict:
    d = {}
    for zone_norm, grp in master_df.groupby("Zone_norm"):
        d[zone_norm] = set(grp["Center_norm"])
    return d


# ---------------------------------------------------------------------------
# Zone assignment — for submitted zone values that don't match any known zone.
# Persisted as a simple raw_zone -> correct_zone mapping (flat file, plenty
# for the handful of entries this will ever have).
# ---------------------------------------------------------------------------

def load_zone_assignments() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f).get("zone_assignments", {})
        except Exception:
            return {}
    return {}


def save_zone_assignment(raw_zone: str, correct_zone: str) -> None:
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {}
    zone_assignments = data.get("zone_assignments", {})
    zone_assignments[raw_zone] = correct_zone
    data["zone_assignments"] = zone_assignments
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def apply_zone_assignments(df: pd.DataFrame, zone_assignments: dict) -> pd.DataFrame:
    if not zone_assignments:
        return df
    df = df.copy()
    lookup = {_normalize(k): v for k, v in zone_assignments.items()}
    df["ZONE NAME"] = df["ZONE NAME"].apply(lambda z: lookup.get(_normalize(z), z))
    return df


def find_unmatched_zones(cleaned_df: pd.DataFrame, master_df: pd.DataFrame) -> list:
    """Unique submitted zone values that don't match any known zone.
    Returns a list of dicts: zone, suggested_zone (best guess, may be None)."""
    zones = master_zone_set(master_df)
    zone_display = {row["Zone_norm"]: row["Zone"] for _, row in master_df.iterrows()}

    results = []
    for raw_zone in sorted(cleaned_df["ZONE NAME"].unique()):
        nz = _normalize(raw_zone)
        if nz in zones:
            continue
        m = difflib.get_close_matches(nz, zones, n=1, cutoff=0.5)
        suggested = zone_display[m[0]] if m else None
        results.append({"zone": raw_zone, "suggested_zone": suggested})
    return results


# ---------------------------------------------------------------------------
# Center assignment — for submitted center values that don't match any known
# center under their (already-corrected) zone. Kept separate from zone
# assignment on purpose: usually only one of the two is actually wrong, and
# fixing them independently is simpler than picking both at once.
# Persisted as a list of {"zone", "raw_center", "center"} — zone-scoped
# because the same wrong text could need a different fix under different zones.
# ---------------------------------------------------------------------------

def load_center_assignments() -> list:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f).get("center_assignments", [])
        except Exception:
            return []
    return []


def save_center_assignment(zone: str, raw_center: str, correct_center: str) -> None:
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {}
    assignments = data.get("center_assignments", [])
    assignments = [
        a for a in assignments
        if not (_normalize(a["zone"]) == _normalize(zone) and _normalize(a["raw_center"]) == _normalize(raw_center))
    ]
    assignments.append({"zone": zone, "raw_center": raw_center, "center": correct_center})
    data["center_assignments"] = assignments
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def apply_center_assignments(df: pd.DataFrame, center_assignments: list) -> pd.DataFrame:
    """Applied AFTER zone assignments, so the zone half of the lookup key is
    already correct by the time this runs."""
    if not center_assignments:
        return df
    df = df.copy()
    lookup = {
        (_normalize(a["zone"]), _normalize(a["raw_center"])): a["center"]
        for a in center_assignments
    }
    keys = list(zip(df["ZONE NAME"].apply(_normalize), df["CENTER NAME"].apply(_normalize)))
    new_center = df["CENTER NAME"].tolist()
    for i, k in enumerate(keys):
        if k in lookup:
            new_center[i] = lookup[k]
    df["CENTER NAME"] = new_center
    return df


def find_unmatched_centers(cleaned_df: pd.DataFrame, master_df: pd.DataFrame) -> list:
    """
    Unique (zone, center) pairs where the ZONE already matches a known zone,
    but the center doesn't match any center listed under it. Only checks rows
    with a valid zone — fix zone assignment first, center issues for those
    rows will naturally resurface afterward if still unresolved.

    Returns a list of dicts: zone, center, suggested_center (best guess, may be None).
    """
    zones = master_zone_set(master_df)
    by_zone = centers_by_zone(master_df)
    center_display = {(row["Zone_norm"], row["Center_norm"]): row["Center"] for _, row in master_df.iterrows()}

    results = []
    pairs = cleaned_df[["ZONE NAME", "CENTER NAME"]].drop_duplicates()
    for _, row in pairs.iterrows():
        zone, center = row["ZONE NAME"], row["CENTER NAME"]
        nz, nc = _normalize(zone), _normalize(center)

        if nz not in zones:
            continue  # zone itself needs fixing first
        if nc in by_zone.get(nz, set()):
            continue

        suggested = None
        stripped_nc = _strip_suffix_noise(nc)
        if stripped_nc != nc and stripped_nc in by_zone.get(nz, set()):
            suggested = center_display.get((nz, stripped_nc))
        else:
            m = difflib.get_close_matches(nc, by_zone.get(nz, set()), n=1, cutoff=0.5)
            if m:
                suggested = center_display.get((nz, m[0]))

        results.append({"zone": zone, "center": center, "suggested_center": suggested})
    return results


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
    date_cols = list(pd.date_range(start_date, end_date, freq="D").date)

    # Filter on the original datetime64 column (against pd.Timestamp) BEFORE
    # deriving plain date objects — filtering on an already-derived .dt.date
    # column can end up comparing incompatible dtypes when the zone has zero
    # matching rows to begin with.
    sub = cleaned_df[cleaned_df["ZONE NAME"].apply(_normalize) == nz].copy()
    sub = sub[(sub["Date"] >= pd.Timestamp(start_date)) & (sub["Date"] <= pd.Timestamp(end_date))]

    if sub.empty:
        counts = pd.DataFrame(0, index=zone_centers["Center_norm"], columns=date_cols)
    else:
        sub["_center_norm"] = sub["CENTER NAME"].apply(_normalize)
        sub["_date"] = sub["Date"].dt.date
        counts = sub.groupby(["_center_norm", "_date"]).size().unstack(fill_value=0)
        counts = counts.reindex(columns=date_cols, fill_value=0)

    result = zone_centers.set_index("Center_norm").join(counts, how="left").fillna(0)
    result = result.rename(columns={c: c.strftime("%d %b") for c in date_cols})
    result = result.reset_index(drop=True)
    for c in result.columns:
        if c != "Center":
            result[c] = result[c].astype(int)
    return result.sort_values("Center").reset_index(drop=True)