"""
Fetch form-response data directly from a publicly viewable Google Sheet,
instead of requiring a manual file upload.

Works when the sheet is shared as "Anyone with the link can view" — no
Google API credentials needed, since we use the plain CSV export endpoint.
"""
import re
import json
import os
import pandas as pd

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def extract_sheet_id(url_or_id: str) -> str:
    """Accepts a full Google Sheets URL or a bare sheet ID and returns the ID."""
    url_or_id = url_or_id.strip()
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    if match:
        return match.group(1)
    # assume they pasted the bare ID already
    return url_or_id


def extract_gid(url_or_id: str) -> str:
    """Extract the gid (sheet/tab id) from a URL if present, else default '0'."""
    match = re.search(r"[?&#]gid=([0-9]+)", url_or_id)
    if match:
        return match.group(1)
    return "0"


def to_csv_export_url(url_or_id: str) -> str:
    sheet_id = extract_sheet_id(url_or_id)
    gid = extract_gid(url_or_id)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def fetch_google_sheet(url_or_id: str) -> pd.DataFrame:
    """Fetch the sheet as a DataFrame. Raises if the sheet isn't public/reachable."""
    csv_url = to_csv_export_url(url_or_id)
    try:
        df = pd.read_csv(csv_url)
    except Exception as e:
        raise RuntimeError(
            "Couldn't fetch the Google Sheet. Make sure it's shared as "
            "'Anyone with the link can view' and the URL is correct. "
            f"Original error: {e}"
        )
    return df


def load_saved_url() -> str:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f).get("google_sheet_url", "")
        except Exception:
            return ""
    return ""


def save_url(url: str) -> None:
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump({"google_sheet_url": url}, f)
    except Exception:
        pass  # non-fatal if we can't persist, e.g. read-only filesystem
