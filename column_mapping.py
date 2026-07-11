"""
Maps whatever column headers actually appear in an uploaded/fetched sheet to
the canonical fields this app needs — so small drift in the form (renamed
headers, reordered columns, minor wording changes) doesn't break anything.

Strategy:
1. Exact match (case/whitespace-insensitive) against known aliases -> confident
2. Fuzzy match against known aliases -> suggested, needs user confirmation
3. Anything left unmapped -> surfaced to the user to pick manually
4. Confirmed mappings are persisted to config.json so they're remembered next time
"""
import re
import json
import os
import difflib

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

# Canonical field -> list of known aliases (lowercase, whitespace-normalized)
CANONICAL_FIELDS = {
    "Timestamp": ["timestamp", "submitted at", "submission time", "time submitted"],
    "ZONE NAME": ["zone name", "zone", "region", "region name"],
    "CENTER NAME": ["center name", "centre name", "center", "centre", "registration center", "registration centre"],
    "Date": ["date", "reporting date", "activity date", "report date"],
    "Total Male Births Registered": [
        "total male births registered", "male births registered", "males registered",
        "total males registered", "male birth registrations",
    ],
    "Total Female Births Registered": [
        "total female births registered", "female births registered", "females registered",
        "total females registered", "female birth registrations",
    ],
    "Total Males Processed": [
        "total males processed", "males processed", "male nid", "males nid processed",
        "total male nid registrations", "male national id processed",
    ],
    "Total Females Processed": [
        "total females processed", "females processed", "female nid", "females nid processed",
        "total female nid registrations", "female national id processed",
    ],
}

REQUIRED_FIELDS = list(CANONICAL_FIELDS.keys())


def _normalize(s: str) -> str:
    s = str(s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[_\-]+", " ", s)
    return s


def suggest_mapping(actual_headers: list, cutoff: float = 0.55):
    """
    Given actual column headers from an uploaded file, suggest a mapping to
    canonical fields.

    Returns:
        confident: dict {actual_header: canonical_field} for exact/near-exact matches
        suggestions: dict {canonical_field: [(actual_header, score), ...]} for fields
                      that need user confirmation (fuzzy match only, or multiple candidates)
        unmapped: list of canonical fields with no reasonable match found
    """
    norm_headers = {h: _normalize(h) for h in actual_headers}
    confident = {}
    suggestions = {}
    used_headers = set()

    for canonical, aliases in CANONICAL_FIELDS.items():
        alias_set = set(aliases) | {_normalize(canonical)}
        # 1. exact normalized match
        exact_match = None
        for h, norm_h in norm_headers.items():
            if h in used_headers:
                continue
            if norm_h in alias_set:
                exact_match = h
                break
        if exact_match:
            confident[exact_match] = canonical
            used_headers.add(exact_match)
            continue

        # 2. fuzzy match against aliases
        candidates = []
        for h, norm_h in norm_headers.items():
            if h in used_headers:
                continue
            best_score = max(
                difflib.SequenceMatcher(None, norm_h, alias).ratio() for alias in alias_set
            )
            if best_score >= cutoff:
                candidates.append((h, round(best_score, 3)))
        candidates.sort(key=lambda x: -x[1])

        if candidates:
            suggestions[canonical] = candidates
        else:
            suggestions[canonical] = []

    unmapped = [
        c for c in REQUIRED_FIELDS
        if c not in confident.values() and not suggestions.get(c)
    ]
    return confident, suggestions, unmapped


def load_saved_mapping() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f).get("column_mapping", {})
        except Exception:
            return {}
    return {}


def save_mapping(mapping: dict) -> None:
    """mapping: {actual_header: canonical_field}"""
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
        except Exception:
            data = {}
    data["column_mapping"] = mapping
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def apply_mapping(df, mapping: dict):
    """Rename df columns according to mapping {actual_header: canonical_field},
    keeping only columns that map to a canonical field."""
    valid_mapping = {k: v for k, v in mapping.items() if k in df.columns}
    df = df.rename(columns=valid_mapping)
    keep_cols = [c for c in REQUIRED_FIELDS if c in df.columns]
    return df[keep_cols]
