"""
Minimal gspread stub — enough for contest_sheet.py + fake_worksheet.py
to import without a live Google Sheets connection.
"""

class Cell:
    def __init__(self, row, col, value):
        self.row = row
        self.col = col
        self.value = value

class exceptions:
    class WorksheetNotFound(Exception):
        pass
    class SpreadsheetNotFound(Exception):
        pass

class utils:
    @staticmethod
    def rowcol_to_a1(row, col):
        """Convert (row, col) 1-based to A1 notation."""
        letters = ""
        c = col
        while c > 0:
            c, remainder = divmod(c - 1, 26)
            letters = chr(65 + remainder) + letters
        return f"{letters}{row}"
