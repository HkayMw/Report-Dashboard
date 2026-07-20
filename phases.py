"""
Phase (activity/campaign) management.

A "phase" is one data-collection activity: its own title, date window,
Google Form/Sheet URL, master reference list, and zone/center assignments.
Creating a new phase from the admin UI re-targets the whole app to a new
location/time period with no code changes.

Stored in phases.json (flat file, same pattern as config.json — plenty for
a handful of phases). Per-phase master reference CSVs live in masters/.

Migration: on first run, the pre-phases config.json campaign (sheet URL +
zone/center assignments, with the original hardcoded activity dates) is
adopted as phase 1 automatically, so existing deployments keep working
with zero manual steps.
"""
import os
import json
import uuid
from datetime import date

PHASES_PATH = os.path.join(os.path.dirname(__file__), "phases.json")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
MASTERS_DIR = os.path.join(os.path.dirname(__file__), "masters")
DEFAULT_MASTER = "master_reference.csv"  # relative to this file's directory

# The original (pre-phases) campaign, used only to migrate an existing
# config.json into phase 1.
_LEGACY_TITLE = "NID & NRBC Registration — Mzuzu City / Mzimba North, Jul 2026"
_LEGACY_START = "2026-07-08"
_LEGACY_END = "2026-07-18"

REQUIRED_MASTER_COLUMNS = ["District", "Zone", "Center"]


def _save(data: dict) -> None:
    try:
        with open(PHASES_PATH, "w") as f:
            json.dump(data, f, indent=1)
    except Exception:
        pass


def _migrate_legacy() -> dict:
    legacy = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                legacy = json.load(f)
        except Exception:
            legacy = {}
    phase = {
        "id": "phase-1",
        "title": _LEGACY_TITLE,
        "start_date": _LEGACY_START,
        "end_date": _LEGACY_END,
        "google_sheet_url": legacy.get("google_sheet_url", ""),
        "master_path": DEFAULT_MASTER,
        "zone_assignments": legacy.get("zone_assignments", {}),
        "center_assignments": legacy.get("center_assignments", []),
    }
    data = {"active_phase": "phase-1", "phases": [phase]}
    _save(data)
    return data


def _load() -> dict:
    if os.path.exists(PHASES_PATH):
        try:
            with open(PHASES_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return _migrate_legacy()


def list_phases() -> list:
    return _load()["phases"]


def get_active_phase() -> dict:
    data = _load()
    for p in data["phases"]:
        if p["id"] == data.get("active_phase"):
            return p
    return data["phases"][0]


def set_active_phase(phase_id: str) -> None:
    data = _load()
    if any(p["id"] == phase_id for p in data["phases"]):
        data["active_phase"] = phase_id
        _save(data)


def phase_dates(phase: dict):
    """Parsed (start_date, end_date) for a phase."""
    return date.fromisoformat(phase["start_date"]), date.fromisoformat(phase["end_date"])


def master_path_for(phase: dict) -> str:
    """Absolute path of the phase's master reference CSV."""
    return os.path.join(os.path.dirname(__file__), phase.get("master_path", DEFAULT_MASTER))


def create_phase(title: str, start_date: date, end_date: date,
                 google_sheet_url: str = "", master_csv_bytes: bytes = None) -> dict:
    """Create a phase; when no master CSV is uploaded the bundled default
    master list is reused (same registry, new time period)."""
    data = _load()
    phase_id = f"phase-{uuid.uuid4().hex[:8]}"
    master_path = DEFAULT_MASTER
    if master_csv_bytes:
        os.makedirs(MASTERS_DIR, exist_ok=True)
        master_path = os.path.join("masters", f"{phase_id}.csv")
        with open(os.path.join(os.path.dirname(__file__), master_path), "wb") as f:
            f.write(master_csv_bytes)
    phase = {
        "id": phase_id,
        "title": title,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "google_sheet_url": google_sheet_url,
        "master_path": master_path,
        "zone_assignments": {},
        "center_assignments": [],
    }
    data["phases"].append(phase)
    _save(data)
    return phase


def update_active_phase(**fields) -> None:
    """Update simple fields (e.g. google_sheet_url) on the active phase."""
    data = _load()
    for p in data["phases"]:
        if p["id"] == data.get("active_phase"):
            p.update(fields)
            break
    _save(data)


def update_phase(phase_id: str, title: str = None, start_date: date = None,
                 end_date: date = None, google_sheet_url: str = None,
                 master_csv_bytes: bytes = None) -> bool:
    """Edit any phase's fields; only the arguments provided are changed.
    A new master CSV replaces the phase's list (stored under masters/)."""
    data = _load()
    for p in data["phases"]:
        if p["id"] != phase_id:
            continue
        if title is not None:
            p["title"] = title
        if start_date is not None:
            p["start_date"] = start_date.isoformat()
        if end_date is not None:
            p["end_date"] = end_date.isoformat()
        if google_sheet_url is not None:
            p["google_sheet_url"] = google_sheet_url
        if master_csv_bytes:
            os.makedirs(MASTERS_DIR, exist_ok=True)
            master_path = os.path.join("masters", f"{phase_id}.csv")
            with open(os.path.join(os.path.dirname(__file__), master_path), "wb") as f:
                f.write(master_csv_bytes)
            p["master_path"] = master_path
        _save(data)
        return True
    return False


def delete_phase(phase_id: str):
    """Delete a phase (and its own master CSV, if it has one). The last
    remaining phase can't be deleted; deleting the active phase activates
    the first remaining one. Returns (ok, message)."""
    data = _load()
    if len(data["phases"]) <= 1:
        return False, "Can't delete the only phase — create another one first."
    target = next((p for p in data["phases"] if p["id"] == phase_id), None)
    if target is None:
        return False, "Phase not found."
    data["phases"] = [p for p in data["phases"] if p["id"] != phase_id]
    if data.get("active_phase") == phase_id:
        data["active_phase"] = data["phases"][0]["id"]
    _save(data)
    # Remove the phase's private master CSV (never the shared default list).
    mp = target.get("master_path", "")
    if mp and mp != DEFAULT_MASTER:
        try:
            os.remove(os.path.join(os.path.dirname(__file__), mp))
        except OSError:
            pass
    return True, f"Phase '{target['title']}' deleted."


# ---------------------------------------------------------------------------
# Phase-scoped zone/center assignments — same semantics as the legacy
# config.json versions in master_reference.py, but namespaced per phase so
# one activity's typo fixes never leak into another's.
# ---------------------------------------------------------------------------

def load_zone_assignments() -> dict:
    return get_active_phase().get("zone_assignments", {})


def save_zone_assignment(raw_zone: str, correct_zone: str) -> None:
    data = _load()
    for p in data["phases"]:
        if p["id"] == data.get("active_phase"):
            p.setdefault("zone_assignments", {})[raw_zone] = correct_zone
            break
    _save(data)


def load_center_assignments() -> list:
    return get_active_phase().get("center_assignments", [])


def save_center_assignment(zone: str, raw_center: str, correct_center: str) -> None:
    def norm(s):
        return " ".join(str(s).strip().upper().split())
    data = _load()
    for p in data["phases"]:
        if p["id"] == data.get("active_phase"):
            assignments = p.setdefault("center_assignments", [])
            assignments[:] = [
                a for a in assignments
                if not (norm(a["zone"]) == norm(zone) and norm(a["raw_center"]) == norm(raw_center))
            ]
            assignments.append({"zone": zone, "raw_center": raw_center, "center": correct_center})
            break
    _save(data)
