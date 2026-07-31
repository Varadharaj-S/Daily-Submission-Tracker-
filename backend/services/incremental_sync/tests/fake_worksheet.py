"""
services/incremental_sync/tests/fake_worksheet.py — in-memory worksheet
with real grid + merge-range semantics, used to test sheet_ops.py's
date-block logic without a live Google Sheets connection.

Tracks merged ranges explicitly (as a set of (start_row, end_row, col)
tuples) so tests can assert exactly which ranges are merged/unmerged —
that's the part most likely to have an off-by-one bug, so it's asserted
directly rather than just checking final cell values.
"""

import re


class FakeWorksheet:
    def __init__(self):
        self.rows = [["DATE", "PROGRAM TITLE", "LINK", "DIFFICULTY", "PLATFORM", "TOPIC", "COUNT"]]
        self.merges = set()  # {(col_letter, start_row, end_row)}

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_rows(self, rows, value_input_option=None):
        for r in rows:
            self.rows.append(list(r))

    def update_cell(self, row, col, value):
        while len(self.rows) < row:
            self.rows.append([])
        r = self.rows[row - 1]
        while len(r) < col:
            r.append("")
        r[col - 1] = value

    def merge_cells(self, cell_range):
        col, start, end = self._parse_range(cell_range)
        self.merges.add((col, start, end))

    def unmerge_cells(self, cell_range):
        col, start, end = self._parse_range(cell_range)
        if (col, start, end) not in self.merges:
            raise ValueError(f"not merged: {cell_range}")
        self.merges.discard((col, start, end))

    @staticmethod
    def _parse_range(cell_range):
        m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", cell_range)
        assert m, f"bad range: {cell_range}"
        col_a, row_a, col_b, row_b = m.groups()
        assert col_a == col_b, "only single-column ranges supported by this fake"
        return col_a, int(row_a), int(row_b)
