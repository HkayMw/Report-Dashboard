"""
Loads the authoritative master list of zones and centers (from the campaign's
official school/center registry PDFs) and provides:
  - separate zone-name and center-name validation against that master list
  - two independent, persisted admin-assignment mechanisms (zone and center
    kept separate — usually only one of the two is actually wrong, so fixing
    them independently is simpler than picking both at once)
  - a lightweight per-zone submission matrix (center x day)

The default reference is bundled with the app (master_reference.csv);
phases can supply their own CSV via phases.py. Assignment persistence is
phase-scoped and lives in phases.json (see phases.py) — the functions here
are pure apply/find helpers.
"""
import os
import re
import difflib
import pandas as pd

REFERENCE_PATH = os.path.join(os.path.dirname(__file__), "master_reference.csv")


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


def load_master(path: str = REFERENCE_PATH) -> pd.DataFrame:
    """Load a master reference CSV (District, Zone, Center). Defaults to the
    bundled list; phases can point at their own CSV via phases.py."""
    df = pd.read_csv(path)
    df["Zone_norm"] = df["Zone"].apply(_normalize)
    df["Center_norm"] = df["Center"].apply(_normalize)
    return df


def district_options(master_df: pd.DataFrame) -> list:
    return sorted(master_df["District"].dropna().unique())


def filter_by_district(cleaned_df: pd.DataFrame, master_df: pd.DataFrame, district: str):
    """Scope both the submitted data and the master list to one district.

    A submitted row belongs to a district via its (assignment-corrected) zone,
    looked up against the master list — the form itself never asks for a
    district. Rows whose zone doesn't match any master zone can't be placed in
    a district, so they are excluded from single-district views (they still
    appear under ALL); resolving them via Zone Assignment brings them in.
    """
    zone_district = dict(zip(master_df["Zone_norm"], master_df["District"]))
    mask = cleaned_df["ZONE NAME"].apply(_normalize).map(zone_district) == district
    return cleaned_df[mask], master_df[master_df["District"] == district]


def normalize_name(s) -> str:
    """Public name normalizer for callers that need to match submitted
    zone/center text against the master list (same rules used internally)."""
    return _normalize(s)


def districts_for_zones(zones, master_df: pd.DataFrame) -> list:
    """Map an iterable of zone names to their master-list district
    ('' for zones that don't match any master zone)."""
    lookup = dict(zip(master_df["Zone_norm"], master_df["District"]))
    return [lookup.get(_normalize(z), "") for z in zones]


def districts_for_centers(centers, master_df: pd.DataFrame) -> list:
    """Map an iterable of center names to their master-list district
    ('' for centers that don't match any master center)."""
    lookup = dict(zip(master_df["Center_norm"], master_df["District"]))
    return [lookup.get(_normalize(c), "") for c in centers]


def master_zone_set(master_df: pd.DataFrame) -> set:
    return set(master_df["Zone_norm"])


def master_center_set(master_df: pd.DataFrame) -> set:
    return set(master_df["Center_norm"])


def centers_by_zone(master_df: pd.DataFrame) -> dict:
    d = {}
    for zone_norm, grp in master_df.groupby("Zone_norm"):
        d[zone_norm] = set(grp["Center_norm"])
    return d


# ---------------------------------------------------------------------------
# Zone assignment — for submitted zone values that don't match any known zone.
# Persistence is phase-scoped and lives in phases.py (phases.json); the
# apply/find functions here are pure and storage-agnostic.
# ---------------------------------------------------------------------------

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
# Stored as a list of {"zone", "raw_center", "center"} — zone-scoped because
# the same wrong text could need a different fix under different zones.
# Persistence is phase-scoped and lives in phases.py (phases.json).
# ---------------------------------------------------------------------------

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
    Unique (zone, center) pairs where the center doesn't match any known
    center — checked independently of whether the zone itself is valid, so
    center assignment isn't blocked by an unrelated zone issue. When the
    submitted zone IS valid, matching/suggestions are scoped to that zone's
    centers; when it isn't, they fall back to the full center list.

    Returns a list of dicts: zone, center, suggested_center (best guess, may be None).
    """
    zones = master_zone_set(master_df)
    all_centers = master_center_set(master_df)
    by_zone = centers_by_zone(master_df)
    center_display = {(row["Zone_norm"], row["Center_norm"]): row["Center"] for _, row in master_df.iterrows()}
    center_display_any = {row["Center_norm"]: row["Center"] for _, row in master_df.iterrows()}

    results = []
    pairs = cleaned_df[["ZONE NAME", "CENTER NAME"]].drop_duplicates()
    for _, row in pairs.iterrows():
        zone, center = row["ZONE NAME"], row["CENTER NAME"]
        nz, nc = _normalize(zone), _normalize(center)

        zone_is_valid = nz in zones
        candidate_centers = by_zone.get(nz, set()) if zone_is_valid else all_centers

        if nc in candidate_centers:
            continue

        suggested = None
        stripped_nc = _strip_suffix_noise(nc)
        if stripped_nc != nc and stripped_nc in candidate_centers:
            suggested = center_display.get((nz, stripped_nc)) if zone_is_valid else center_display_any.get(stripped_nc)
        else:
            m = difflib.get_close_matches(nc, candidate_centers, n=1, cutoff=0.5)
            if m:
                suggested = center_display.get((nz, m[0])) if zone_is_valid else center_display_any.get(m[0])

        results.append({"zone": zone, "center": center, "suggested_center": suggested})
    return results


# ---------------------------------------------------------------------------
# Zone-scoped submission matrix (center x day) — deliberately scoped to one
# zone at a time (rather than all 333 centers) to keep this cheap: a handful
# of centers by a fixed ~11-day window is a small pivot, not a heavy computation.
# ---------------------------------------------------------------------------

def completion_report(cleaned_df: pd.DataFrame, master_df: pd.DataFrame, start_date, end_date):
    """
    Reporting completion for EVERY predefined zone and center (not just one
    zone at a time, unlike zone_center_matrix above).

    Only counts a submission if its (zone, center) pair matches a predefined
    master-list pair exactly (after config.json's zone/center assignments have
    already been applied to cleaned_df by the caller) — a submission whose
    zone or center hasn't been assigned/mapped yet is excluded entirely, so
    completion figures never get inflated by not-yet-reconciled data.

    Returns (district_df, zone_df, center_df):
      center_df: one row per predefined center — District, Zone, Center,
                 Days Expected, Days Submitted, Completion %
      zone_df: one row per predefined zone — aggregated across its centers
      district_df: one row per district — aggregated across its zones
    """
    date_cols = list(pd.date_range(start_date, end_date, freq="D").date)
    n_days = len(date_cols)

    sub = cleaned_df[
        (cleaned_df["Date"] >= pd.Timestamp(start_date)) & (cleaned_df["Date"] <= pd.Timestamp(end_date))
    ].copy()
    sub["_zone_norm"] = sub["ZONE NAME"].apply(_normalize)
    sub["_center_norm"] = sub["CENTER NAME"].apply(_normalize)
    sub["_date"] = sub["Date"].dt.date

    valid_pairs = set(zip(master_df["Zone_norm"], master_df["Center_norm"]))
    pairs = pd.Series(list(zip(sub["_zone_norm"], sub["_center_norm"])), index=sub.index)
    sub = sub[pairs.isin(valid_pairs)]

    submitted_days = sub.groupby(["_zone_norm", "_center_norm"])["_date"].nunique()

    centers = master_df[["District", "Zone", "Center", "Zone_norm", "Center_norm"]].drop_duplicates()
    center_rows = []
    for _, row in centers.iterrows():
        key = (row["Zone_norm"], row["Center_norm"])
        submitted = int(submitted_days.get(key, 0))
        center_rows.append({
            "District": row["District"],
            "Zone": row["Zone"],
            "Center": row["Center"],
            "Days Expected": n_days,
            "Days Submitted": submitted,
            "Completion %": round(100 * submitted / n_days, 1) if n_days else 0.0,
        })
    center_df = pd.DataFrame(center_rows).sort_values(["District", "Zone", "Center"]).reset_index(drop=True)

    zone_df = center_df.groupby("Zone").agg(
        District=("District", "first"),
        Centers=("Center", "count"),
        **{"Total Days Expected": ("Days Expected", "sum"), "Total Days Submitted": ("Days Submitted", "sum")},
    ).reset_index()
    zone_df["Completion %"] = (
        100 * zone_df["Total Days Submitted"] / zone_df["Total Days Expected"]
    ).round(1)
    zone_df = zone_df[["District", "Zone", "Centers", "Total Days Expected", "Total Days Submitted", "Completion %"]]
    zone_df = zone_df.sort_values(["District", "Zone"]).reset_index(drop=True)

    district_df = center_df.groupby("District").agg(
        Zones=("Zone", "nunique"),
        Centers=("Center", "count"),
        **{"Total Days Expected": ("Days Expected", "sum"), "Total Days Submitted": ("Days Submitted", "sum")},
    ).reset_index()
    district_df["Completion %"] = (
        100 * district_df["Total Days Submitted"] / district_df["Total Days Expected"]
    ).round(1)
    district_df = district_df.sort_values("District").reset_index(drop=True)

    return district_df, zone_df, center_df


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
