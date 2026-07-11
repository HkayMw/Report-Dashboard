"""
Loads the authoritative master list of zones and centers (from the campaign's
official school/center registry PDFs) and provides:
  - authoritative typo/mismatch detection for submitted zone/center names
  - "who hasn't reported yet" tracking across the activity's date range

This is a static reference bundled with the app (master_reference.csv).
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
# Stripped iteratively so combinations like "... PRIMARY SCHOOL" reduce fully.
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


def master_zones(master_df: pd.DataFrame) -> list:
    return sorted(master_df["Zone"].unique())


def master_pairs_set(master_df: pd.DataFrame) -> set:
    return set(zip(master_df["Zone_norm"], master_df["Center_norm"]))


def master_zone_set(master_df: pd.DataFrame) -> set:
    return set(master_df["Zone_norm"])


def master_center_set(master_df: pd.DataFrame) -> set:
    return set(master_df["Center_norm"])


def centers_by_zone(master_df: pd.DataFrame) -> dict:
    d = {}
    for zone_norm, grp in master_df.groupby("Zone_norm"):
        d[zone_norm] = set(grp["Center_norm"])
    return d


def zone_for_center(master_df: pd.DataFrame) -> dict:
    """center_norm -> list of zone_norm values it appears under (usually one)."""
    d = {}
    for _, row in master_df.iterrows():
        d.setdefault(row["Center_norm"], []).append(row["Zone_norm"])
    return d


def check_submissions_against_master(cleaned_df: pd.DataFrame, master_df: pd.DataFrame):
    """
    Compare each unique (ZONE NAME, CENTER NAME) pair actually submitted against
    the master reference, and diagnose any mismatch.

    Returns a list of dicts, one per problematic submitted pair:
      {
        "zone": submitted zone as it appears in the data,
        "center": submitted center as it appears in the data,
        "issue": one of "zone_center_swap" | "zone_typo" | "wrong_zone_for_center"
                 | "center_typo" | "unknown",
        "suggestion": human-readable explanation / suggested correct value,
      }
    Pairs that exactly match the master list (after normalization) are omitted.
    """
    zones = master_zone_set(master_df)
    centers = master_center_set(master_df)
    pairs = master_pairs_set(master_df)
    by_zone = centers_by_zone(master_df)
    zone_lookup = zone_for_center(master_df)

    issues = []
    submitted_pairs = cleaned_df[["ZONE NAME", "CENTER NAME"]].drop_duplicates()

    for _, row in submitted_pairs.iterrows():
        zone, center = row["ZONE NAME"], row["CENTER NAME"]
        nz, nc = _normalize(zone), _normalize(center)

        if (nz, nc) in pairs:
            continue  # exact match, nothing to flag

        if nz not in zones:
            # the "zone" value doesn't exist as a zone at all
            if nz in centers:
                actual_zones = zone_lookup.get(nz, [])
                issues.append({
                    "zone": zone, "center": center, "issue": "zone_center_swap",
                    "suggestion": f"'{zone}' is listed as a CENTER (under zone "
                                   f"'{actual_zones[0].title() if actual_zones else '?'}') in the master "
                                   f"reference, not a zone. This looks like a zone/center mix-up.",
                })
                continue
            zone_matches = difflib.get_close_matches(nz, zones, n=1, cutoff=0.6)
            if zone_matches:
                correct_zone = next(z for z in master_df["Zone"].unique() if _normalize(z) == zone_matches[0])
                issues.append({
                    "zone": zone, "center": center, "issue": "zone_typo",
                    "suggestion": f"'{zone}' doesn't match any known zone. Closest match: '{correct_zone}'.",
                })
                continue
            issues.append({
                "zone": zone, "center": center, "issue": "unknown",
                "suggestion": f"'{zone}' doesn't match any known zone or center in the master reference.",
            })
            continue

        # zone is valid, but this center isn't listed under it
        if nc not in by_zone.get(nz, set()):
            if nc in zones:
                issues.append({
                    "zone": zone, "center": center, "issue": "wrong_zone_for_center",
                    "suggestion": f"'{center}' is itself a zone name in the master reference, not a center "
                                   f"under '{zone}'. Possible zone/center mix-up.",
                })
                continue
            other_zones = zone_lookup.get(nc, [])
            if other_zones:
                display_zones = ", ".join(sorted(set(other_zones)))
                issues.append({
                    "zone": zone, "center": center, "issue": "wrong_zone_for_center",
                    "suggestion": f"'{center}' exists in the master reference, but under zone(s) "
                                   f"'{display_zones}', not '{zone}'. Check which is correct.",
                })
                continue
            # Suffix-noise match: e.g. "CHIBAVI PRIMARY SCHOOL" -> "CHIBAVI". This is a much
            # stronger signal than raw string-similarity ratio, since a long common suffix
            # tanks the ratio even when the core name is an exact match, so it's checked first.
            stripped_nc = _strip_suffix_noise(nc)
            if stripped_nc != nc and stripped_nc in by_zone.get(nz, set()):
                correct_center = next(
                    c for c in master_df[master_df["Zone_norm"] == nz]["Center"]
                    if _normalize(c) == stripped_nc
                )
                issues.append({
                    "zone": zone, "center": center, "issue": "center_typo",
                    "suggestion": f"'{center}' doesn't match any known center under '{zone}' exactly, "
                                   f"but matches after removing a common suffix like 'PRIMARY SCHOOL'. "
                                   f"Closest match: '{correct_center}'.",
                })
                continue
            center_matches = difflib.get_close_matches(nc, by_zone.get(nz, set()), n=1, cutoff=0.6)
            if center_matches:
                correct_center = next(
                    c for c in master_df[master_df["Zone_norm"] == nz]["Center"]
                    if _normalize(c) == center_matches[0]
                )
                issues.append({
                    "zone": zone, "center": center, "issue": "center_typo",
                    "suggestion": f"'{center}' doesn't match any known center under '{zone}'. "
                                   f"Closest match: '{correct_center}'.",
                })
                continue
            issues.append({
                "zone": zone, "center": center, "issue": "unknown",
                "suggestion": f"'{center}' doesn't match any known center under '{zone}' in the master reference.",
            })

    return issues


def reporting_status(cleaned_df: pd.DataFrame, master_df: pd.DataFrame, start_date, end_date):
    """
    For each date in [start_date, end_date], determine which master (zone, center)
    pairs have NOT been submitted for that date.

    Returns:
        daily_summary: DataFrame with Date, Reported, Missing, Total, Pct Complete
        missing_by_date: dict {date: [(zone, center), ...]} of missing pairs (display names)
    """
    pairs_df = master_df[["Zone", "Center", "Zone_norm", "Center_norm"]].drop_duplicates()
    total = len(pairs_df)

    sub = cleaned_df.copy()
    sub["_zone_norm"] = sub["ZONE NAME"].apply(_normalize)
    sub["_center_norm"] = sub["CENTER NAME"].apply(_normalize)
    sub["_date"] = sub["Date"].dt.date

    date_range = pd.date_range(start_date, end_date, freq="D").date

    daily_rows = []
    missing_by_date = {}

    for d in date_range:
        submitted_today = set(
            zip(sub.loc[sub["_date"] == d, "_zone_norm"], sub.loc[sub["_date"] == d, "_center_norm"])
        )
        missing_mask = ~pairs_df.apply(
            lambda r: (r["Zone_norm"], r["Center_norm"]) in submitted_today, axis=1
        )
        missing_pairs = list(zip(pairs_df.loc[missing_mask, "Zone"], pairs_df.loc[missing_mask, "Center"]))
        missing_by_date[d] = missing_pairs
        reported = total - len(missing_pairs)
        daily_rows.append({
            "Date": d, "Reported": reported, "Missing": len(missing_pairs),
            "Total": total, "Pct Complete": round(100 * reported / total, 1) if total else 0,
        })

    daily_summary = pd.DataFrame(daily_rows)
    return daily_summary, missing_by_date


def center_completion(cleaned_df: pd.DataFrame, master_df: pd.DataFrame, start_date, end_date):
    """
    For each master (zone, center) pair, how many days in [start_date, end_date]
    did they submit at least one report, out of the full range (same denominator
    for every center, regardless of when they started reporting).

    Returns a DataFrame: Zone, Center, Days Reported, Days Expected, Completion %
    sorted by Completion % descending.
    """
    pairs_df = master_df[["Zone", "Center", "Zone_norm", "Center_norm"]].drop_duplicates().copy()

    sub = cleaned_df.copy()
    sub["_zone_norm"] = sub["ZONE NAME"].apply(_normalize)
    sub["_center_norm"] = sub["CENTER NAME"].apply(_normalize)
    sub["_date"] = sub["Date"].dt.date
    sub = sub[(sub["_date"] >= start_date) & (sub["_date"] <= end_date)]

    days_reported = sub.groupby(["_zone_norm", "_center_norm"])["_date"].nunique()
    total_days = (end_date - start_date).days + 1

    pairs_df["Days Reported"] = pairs_df.apply(
        lambda r: int(days_reported.get((r["Zone_norm"], r["Center_norm"]), 0)), axis=1
    )
    pairs_df["Days Expected"] = total_days
    pairs_df["Completion %"] = (pairs_df["Days Reported"] / total_days * 100).round(1) if total_days else 0.0

    result = pairs_df[["Zone", "Center", "Days Reported", "Days Expected", "Completion %"]]
    result = result.sort_values(["Completion %", "Zone", "Center"], ascending=[False, True, True])
    return result.reset_index(drop=True)


def centers_never_reported(cleaned_df: pd.DataFrame, master_df: pd.DataFrame, start_date, end_date):
    """Master (zone, center) pairs with zero submissions anywhere in [start_date, end_date]."""
    pairs_df = master_df[["Zone", "Center", "Zone_norm", "Center_norm"]].drop_duplicates()

    sub = cleaned_df.copy()
    sub["_zone_norm"] = sub["ZONE NAME"].apply(_normalize)
    sub["_center_norm"] = sub["CENTER NAME"].apply(_normalize)
    sub["_date"] = sub["Date"].dt.date
    sub = sub[(sub["_date"] >= start_date) & (sub["_date"] <= end_date)]

    ever_submitted = set(zip(sub["_zone_norm"], sub["_center_norm"]))
    never_mask = ~pairs_df.apply(
        lambda r: (r["Zone_norm"], r["Center_norm"]) in ever_submitted, axis=1
    )
    return pairs_df.loc[never_mask, ["Zone", "Center"]].sort_values(["Zone", "Center"]).reset_index(drop=True)