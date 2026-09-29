\
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import pandas as pd

from .config import OUTPUT_DIR


def export_results(df: pd.DataFrame, rejected: pd.DataFrame | None = None) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx = OUTPUT_DIR / f"screen_result_{stamp}.xlsx"
    csv = OUTPUT_DIR / f"screen_result_{stamp}.csv"

    df.to_csv(csv, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="筛选通过", index=False)
        if rejected is not None and not rejected.empty:
            rejected.to_excel(writer, sheet_name="全部检查结果", index=False)

        for sheet in writer.book.worksheets:
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for col in sheet.columns:
                max_len = 0
                col_letter = col[0].column_letter
                for cell in col[:200]:
                    value = "" if cell.value is None else str(cell.value)
                    max_len = max(max_len, len(value))
                sheet.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 30)

    return xlsx, csv
