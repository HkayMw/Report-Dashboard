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
