"""
Inspect various rows of the downloaded XLS file to understand the structure.
"""
import xlrd
import os

try:
    file_path = "atmos_usage_data.xls"
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        exit(1)

    workbook = xlrd.open_workbook(file_path)
    sheet = workbook.sheet_by_index(0)

    print(f"Sheet Name: {sheet.name}")
    print(f"Rows: {sheet.nrows}, Cols: {sheet.ncols}")
    
    print("\n--- First 10 Rows ---")
    for row_idx in range(min(10, sheet.nrows)):
        row_values = sheet.row_values(row_idx)
        print(f"Row {row_idx}: {row_values}")

except Exception as e:
    print(f"Error reading XLS: {e}")
