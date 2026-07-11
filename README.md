# NRBC Daily Reporting Dashboard

A local Streamlit app that cleans and analyzes the NRBC daily reporting form
export (Excel), with an interactive dashboard and a one-click Excel report export.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

This opens the app in your browser (usually at http://localhost:8501).

## Data model

The form captures **two separate activities** on the same daily submission —
not a before/after pipeline:
- **Birth Registration** (Section 2): Male/Female births registered that day
- **National ID (NID) Registration** (Section 3): Males/Females who had a
  National ID processed that day (can include adults, unrelated to that
  day's births)

The app tracks these as two independent metrics throughout (KPIs, charts,
exports) rather than computing a "processing rate" between them.

## Admin access vs. viewer access

To stop casual viewers from interfering with the data connection or
accidentally uploading files, the app has two access levels:

- **Viewers** (no login): see the full dashboard — filters, KPIs, charts,
  data-quality info — using whatever data source is already connected.
  They cannot change the Google Sheet link, upload a file, confirm
  zone/center name merges, or export.
- **Admin** (PIN required, entered in the sidebar): unlocks the Data Source
  panel (Google Sheet URL / manual upload), the column-mapping confirmation
  step, the zone/center typo-merge controls, and the Export button.

**First run:** the default admin PIN is `1234`. Log in with it once, then
use the "Change admin PIN" box in the sidebar to set your own — the app
will nag you in the sidebar until you do. The PIN is stored (hashed) in
`config.json` next to the app.

⚠️ This is a basic deterrent, not real security — there's no rate-limiting,
no per-user accounts, and the config file is plain text on disk. Fine for
keeping honest people from poking at the data connection on a shared
machine or local network; not sufficient if you deploy this somewhere
publicly reachable. For that, swap in real auth (e.g. `streamlit-authenticator`,
or put it behind SSO).

## Connecting to Google Sheets (live data, no upload needed)

If your Google Form writes to a Google Sheet, you can point the app straight
at it instead of exporting/uploading a file each time:

1. Open the Google Sheet with the form responses.
2. Click **Share** and set access to **"Anyone with the link" → Viewer**.
   (No Google account/API key needed — the app just reads the public CSV export.)
3. Copy the sheet's URL from your browser (the `.../edit#gid=...` link).
4. In the app, choose **"Google Sheet (live)"** as the data source and paste the URL.

The app will:
- Auto-load the latest data when you open it (cached for 5 minutes so it
  doesn't refetch on every click)
- Refresh immediately if you click **🔄 Refresh now**
- Remember the URL for next time (saved to `config.json` next to the app)

If the sheet is private, this simple method won't work — that requires a
Google service account with proper OAuth credentials, which is a bigger setup.
Let me know if you need that instead.

## Handling changes to the form (column mapping)

The app doesn't require exact column names anymore. If a header gets renamed,
reordered, or slightly reworded (e.g. "Centre" instead of "CENTER NAME"), it
auto-matches based on similarity. If a column can't be confidently matched,
a **Column Mapping** panel appears asking you to pick the right source
column for each required field. Once confirmed, the mapping is remembered
(saved to `config.json`) so you won't be asked again for the same headers.

This makes the app resilient to *drift* in this same form — it does not make
it a generic "any spreadsheet" tool. It still expects the same underlying
concepts (a zone, a center, a date, and the four birth/NID count fields) to
exist somewhere in the file, just not necessarily under the exact same names.

## Usage

1. Upload the `.xlsx` export from the reporting form.
2. Open **Data Quality Review** to see:
   - Auto-removed exact duplicates (kept the latest submission by timestamp)
   - Rows with missing numeric values
   - Rows with non-whole-number values (e.g. `0.01` in a count column —
     these are almost always data entry errors worth following up on)
   - Suspected typo pairs in Zone/Center names (e.g. "Bqengu" vs "Bwengu") —
     pick which one is correct, or "Keep separate" if they're genuinely different.
     Click **Apply confirmed merges** to re-run cleaning with your corrections.
3. Use the sidebar filters (Zone, Center, Date range) to narrow the view.
4. Explore the tabs: By Zone, By Center, Trend Over Time, Male vs Female.
5. Click **Download Excel Report** to export a formatted workbook with:
   - Summary sheet (KPIs + data quality notes)
   - Cleaned Data sheet
   - Per-Zone sheet with Birth Registration + NID Registration charts
   - Per-Center sheet with Birth Registration + NID Registration charts
   - Trend sheet with chart (populates once multiple dates are present)
   - Flagged Rows sheet (duplicates, missing values, non-integer values)

## Notes

- The app expects columns: `Timestamp`, `ZONE NAME`, `CENTER NAME`, `Date`,
  `Total Male Births Registered`, `Total Female Births Registered`,
  `Total Males Processed`, `Total Females Processed`. Extra blank trailing
  columns are dropped automatically.
- Zone/Center name merges you confirm only apply for the current session —
  if you want them to always apply, add them to `confirmed_merges` at the top
  of `app.py`'s initial session state, or fix the source spreadsheet directly.
