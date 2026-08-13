"""
fake_year_sheet_service.py — in-memory replacement for
services/year_sheet_service.py. Maps year -> sheet_id in a plain dict.
Used in year-isolation tests so we don't need Postgres or Google APIs.
"""

_registry = {}  # year (str) -> spreadsheet_id (str)


def configure(year, sheet_id):
    _registry[str(year)] = sheet_id


def clear():
    _registry.clear()


def get_sheet_id_for_year(year):
    return _registry.get(str(year))


def is_year_configured(year):
    return str(year) in _registry


def list_configured_years():
    return list(_registry.keys())
