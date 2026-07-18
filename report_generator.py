"""
Generates a formatted .xlsx export: cleaned data, summary, per-zone,
per-center sheets, with native Excel charts.

Model note: Birth Registration and NID (National ID) Registration are two
SEPARATE activities captured on the same daily form - not a before/after
pipeline. They are reported side-by-side throughout, never as a "rate".
"""
import io
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, LineChart, Reference

from data_processing import NUMERIC_COLS

HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)

EXPORT_COLS = [
    "Timestamp", "ZONE NAME", "CENTER NAME", "Date",
    "Total Male Births Registered", "Total Female Births Registered",
    "Total Males Processed", "Total Females Processed",
    "Total Birth Registrations", "Total NID Registrations",
]


def _write_df(ws, df, start_row=1, index=False):
    """Write a dataframe to a worksheet starting at start_row, styled header."""
    cols = ([df.index.name or ""] if index else []) + list(df.columns)
    for j, col_name in enumerate(cols, start=1):
        cell = ws.cell(row=start_row, column=j, value=str(col_name))
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")

    for i, (idx, row) in enumerate(df.iterrows(), start=start_row + 1):
        offset = 1
        if index:
            ws.cell(row=i, column=1, value=idx)
            offset = 2
        for j, val in enumerate(row, start=offset):
            if pd.isna(val):
                val = None
            elif hasattr(val, "isoformat"):
                val = val.isoformat()
            ws.cell(row=i, column=j, value=val)

    for j, col_name in enumerate(cols, start=1):
        max_len = max(
            [len(str(col_name))] + [len(str(v)) for v in (df.index if index and j == 1 else df.iloc[:, j - (2 if index else 1)])]
        ) if len(df) else len(str(col_name))
        ws.column_dimensions[get_column_letter(j)].width = min(max(max_len + 2, 10), 40)

    return start_row + len(df) + 1  # next free row


def build_report(cleaned_df: pd.DataFrame, report: dict, zone_summary: pd.DataFrame,
                  center_summary: pd.DataFrame, kpis: dict) -> bytes:
    wb = Workbook()

    # ---------- Summary sheet ----------
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "NID & NRBC Daily Reporting — Summary"
    ws["A1"].font = Font(bold=True, size=14)

    row = 3
    for label, value in kpis.items():
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        ws.cell(row=row, column=2, value=value)
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="Data Quality Notes").font = Font(bold=True, size=12)
    row += 1
    notes = [
        f"Duplicate/resubmitted rows removed (kept latest by timestamp): {report.get('duplicates_removed_count', 0)}",
        f"Rows with missing numeric values (filled with 0): {len(report.get('missing_numeric_rows', []))}",
        f"Rows with invalid/negative numbers: {len(report.get('negative_rows', []))}",
        f"Rows with non-whole-number values (likely data entry errors, e.g. 0.01): {len(report.get('non_integer_rows', []))}",
    ]
    for note in notes:
        ws.cell(row=row, column=1, value=note)
        row += 1

    ws.column_dimensions["A"].width = 45
    ws.column_dimensions["B"].width = 20

    # ---------- Cleaned Data sheet ----------
    ws2 = wb.create_sheet("Cleaned Data")
    _write_df(ws2, cleaned_df[EXPORT_COLS])
    ws2.freeze_panes = "A2"

    # ---------- Per-Zone sheet ----------
    ws3 = wb.create_sheet("Per-Zone")
    next_row = _write_df(ws3, zone_summary.reset_index())
    n = len(zone_summary)
    chart = BarChart()
    chart.title = "Birth Registrations by Zone"
    chart.y_axis.title = "Births Registered"
    chart.x_axis.title = "Zone"
    col_birth = zone_summary.reset_index().columns.get_loc("Total Birth Registrations") + 1
    data = Reference(ws3, min_col=col_birth, min_row=1, max_row=n + 1, max_col=col_birth)
    cats = Reference(ws3, min_col=1, min_row=2, max_row=n + 1)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.width, chart.height = 20, 10
    ws3.add_chart(chart, f"A{next_row + 2}")

    chart_nid = BarChart()
    chart_nid.title = "NID Registrations by Zone"
    chart_nid.y_axis.title = "NID Registrations"
    chart_nid.x_axis.title = "Zone"
    col_nid = zone_summary.reset_index().columns.get_loc("Total NID Registrations") + 1
    data_nid = Reference(ws3, min_col=col_nid, min_row=1, max_row=n + 1, max_col=col_nid)
    chart_nid.add_data(data_nid, titles_from_data=True)
    chart_nid.set_categories(cats)
    chart_nid.width, chart_nid.height = 20, 10
    ws3.add_chart(chart_nid, f"J{next_row + 2}")

    # ---------- Per-Center sheet ----------
    ws4 = wb.create_sheet("Per-Center")
    next_row2 = _write_df(ws4, center_summary.reset_index())
    n2 = len(center_summary)
    chart2 = BarChart()
    chart2.title = "Birth Registrations by Center"
    chart2.y_axis.title = "Births Registered"
    chart2.x_axis.title = "Center"
    col_birth2 = center_summary.reset_index().columns.get_loc("Total Birth Registrations") + 1
    data2 = Reference(ws4, min_col=col_birth2, min_row=1, max_row=n2 + 1, max_col=col_birth2)
    cats2 = Reference(ws4, min_col=1, min_row=2, max_row=n2 + 1)
    chart2.add_data(data2, titles_from_data=True)
    chart2.set_categories(cats2)
    chart2.width, chart2.height = 24, 12
    ws4.add_chart(chart2, f"A{next_row2 + 2}")

    chart2_nid = BarChart()
    chart2_nid.title = "NID Registrations by Center"
    chart2_nid.y_axis.title = "NID Registrations"
    chart2_nid.x_axis.title = "Center"
    col_nid2 = center_summary.reset_index().columns.get_loc("Total NID Registrations") + 1
    data2_nid = Reference(ws4, min_col=col_nid2, min_row=1, max_row=n2 + 1, max_col=col_nid2)
    chart2_nid.add_data(data2_nid, titles_from_data=True)
    chart2_nid.set_categories(cats2)
    chart2_nid.width, chart2_nid.height = 24, 12
    ws4.add_chart(chart2_nid, f"J{next_row2 + 2}")

    # ---------- Trend sheet (by date) ----------
    trend = cleaned_df.groupby(cleaned_df["Date"].dt.date).agg(
        Total_Birth_Registrations=("Total Birth Registrations", "sum"),
        Total_NID_Registrations=("Total NID Registrations", "sum"),
    ).reset_index()
    trend.columns = ["Date", "Total Birth Registrations", "Total NID Registrations"]
    ws5 = wb.create_sheet("Trend")
    next_row3 = _write_df(ws5, trend)
    if len(trend) > 1:
        chart3 = LineChart()
        chart3.title = "Birth & NID Registrations Over Time"
        chart3.y_axis.title = "Count"
        chart3.x_axis.title = "Date"
        n3 = len(trend)
        data3 = Reference(ws5, min_col=2, min_row=1, max_row=n3 + 1, max_col=3)
        cats3 = Reference(ws5, min_col=1, min_row=2, max_row=n3 + 1)
        chart3.add_data(data3, titles_from_data=True)
        chart3.set_categories(cats3)
        chart3.width, chart3.height = 22, 10
        ws5.add_chart(chart3, f"A{next_row3 + 2}")

    # ---------- Flagged rows sheet ----------
    ws6 = wb.create_sheet("Flagged Rows")
    r = 1
    ws6.cell(row=r, column=1, value="Duplicate/Resubmitted Rows Found").font = Font(bold=True, size=12)
    r += 1
    dupes = report.get("duplicates_found", pd.DataFrame())
    if len(dupes):
        r = _write_df(ws6, dupes[["Timestamp", "ZONE NAME", "CENTER NAME", "Date"] + NUMERIC_COLS], start_row=r) + 1
    else:
        ws6.cell(row=r, column=1, value="None found")
        r += 2

    ws6.cell(row=r, column=1, value="Rows with Missing Numeric Values").font = Font(bold=True, size=12)
    r += 1
    missing = report.get("missing_numeric_rows", pd.DataFrame())
    if len(missing):
        cols_present = [c for c in EXPORT_COLS if c in missing.columns]
        r = _write_df(ws6, missing[cols_present], start_row=r) + 1
    else:
        ws6.cell(row=r, column=1, value="None found")
        r += 2

    ws6.cell(row=r, column=1, value="Rows with Non-Whole-Number Values (likely data entry errors)").font = Font(bold=True, size=12)
    r += 1
    non_int = report.get("non_integer_rows", pd.DataFrame())
    if len(non_int):
        cols_present = [c for c in EXPORT_COLS if c in non_int.columns]
        r = _write_df(ws6, non_int[cols_present], start_row=r) + 1
    else:
        ws6.cell(row=r, column=1, value="None found")
        r += 2

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_completion_report(zone_df: pd.DataFrame, center_df: pd.DataFrame, start_date, end_date) -> bytes:
    """Excel export for master_reference.completion_report()'s output: one
    sheet per predefined zone, one per predefined center — figures only
    reflect submissions already mapped to the predefined master list."""
    wb = Workbook()

    ws = wb.active
    ws.title = "Zone Completion"
    ws["A1"] = f"Zone Completion — {start_date} to {end_date}"
    ws["A1"].font = Font(bold=True, size=14)
    _write_df(ws, zone_df, start_row=3)

    ws2 = wb.create_sheet("Center Completion")
    ws2["A1"] = f"Center Completion — {start_date} to {end_date}"
    ws2["A1"].font = Font(bold=True, size=14)
    _write_df(ws2, center_df, start_row=3)
    ws2.freeze_panes = "A4"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
