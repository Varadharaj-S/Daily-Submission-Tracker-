"""Minimal psycopg2 stub — just enough for db.py to import."""

class IntegrityError(Exception):
    pass

class errors:
    class UniqueViolation(Exception):
        pass

class extras:
    class RealDictCursor:
        pass

def connect(*a, **kw):
    raise RuntimeError("psycopg2 stub: no real DB connection available in test")
