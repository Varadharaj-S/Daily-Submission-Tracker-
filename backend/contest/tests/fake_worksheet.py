"""
contest/tests/fake_worksheet.py — a minimal in-memory stand-in for a
gspread Worksheet, implementing just the methods contest_sheet.py calls
(row_values, get_all_values, append_row, append_rows, insert_cols,
update). This lets contest_sheet.py's column/row logic be exercised for
real without a live Google Sheets connection.

This is NOT a mock — it's a small real implementation of the same grid
semantics a real sheet has (rows/columns, 1-based indices, A1 ranges for
`update`), so a test passing here means the actual algorithm is correct,
not just that a mock was called with the right arguments.

PHASE 2 FIX: insert_cols(values, col) semantics corrected.
gspread's contract: values is a list of COLUMNS, where each inner list
is one column's values top-to-bottom (values[0] = first column to insert,
values[1] = second column, …). The old implementation treated the outer
list as rows instead of columns, so only row 0 (the header) got the right
value and all student rows got "" instead of "ABS".
"""

import re


class FakeWorksheet:
    def __init__(self):
        self.rows = []  # list of lists; self.rows[0] is the header once created

    def row_values(self, n):
        if n - 1 >= len(self.rows):
            return []
        return list(self.rows[n - 1])

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_row(self, row):
        self.rows.append(list(row))

    def append_rows(self, rows, value_input_option=None):
        for r in rows:
            self.rows.append(list(r))

    def insert_cols(self, values, col):
        """
        values: list of columns to insert.
            values[0] = first column's values, top-to-bottom (index 0 = header row).
            values[1] = second column, etc.
        col: 1-based insertion position — matches gspread's insert_cols semantics.

        Example (one column):
            insert_cols([["LC452", "ABS", "ABS"]], 5)
            inserts "LC452" into row 0 col 4, "ABS" into rows 1 and 2, col 4.
        """
        idx = col - 1  # 0-based column index to insert at
        # Ensure the grid has enough rows for all values in the first column
        first_col = values[0] if values else []
        while len(self.rows) < len(first_col):
            self.rows.append([])

        for col_offset, col_values in enumerate(values):
            insert_at = idx + col_offset
            for row_i in range(len(self.rows)):
                v = col_values[row_i] if row_i < len(col_values) else ""
                row = self.rows[row_i]
                while len(row) < insert_at:
                    row.append("")
                row.insert(insert_at, v)

    def update(self, cell_range, values, value_input_option=None):
        m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", cell_range)
        assert m, f"bad range: {cell_range}"
        start_col = self._col_to_idx(m.group(1))
        start_row = int(m.group(2))
        for i, row_values in enumerate(values):
            r = start_row - 1 + i
            while len(self.rows) <= r:
                self.rows.append([])
            row = self.rows[r]
            for j, v in enumerate(row_values):
                c = start_col + j
                while len(row) <= c:
                    row.append("")
                row[c] = v

    @staticmethod
    def _col_to_idx(letters):
        n = 0
        for ch in letters:
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n - 1  # 0-based
