"""
routes/recommendations.py — PHASE 3: mentor Recommendation + News system.

One reusable `recommendations` table (see database/db.py's
ensure_recommendation_schema()), isolated per academic year/cohort via
the `cohort_year` column — same pattern Phase 2 used for users.cohort_year.

Two audiences, two sets of routes:

  * Student read routes (`/recommendations`, `/recommendations/<id>`):
    year is NEVER taken from the request. It is always
    `current_user.cohort_year`, read straight off the authenticated
    session's DB row. A student cannot request another year's
    recommendations by changing a query/body/URL parameter because
    there is no such parameter to change — the WHERE clause is built
    server-side from the session, full stop.

  * Mentor management routes (`/admin/recommendations*`): mentors may
    manage every year, but still have to say which one for a given
    request (same "pick a tab" pattern as routes/admin.py's mentor
    problem-assignment endpoints). `year` is validated against
    services/year_sheet_service.list_configured_years() so a typo
    doesn't silently create a stray cohort bucket.

`category` is intentionally a free-text column, not an enum, so mentors
aren't locked out of a new category without a schema change.
SUGGESTED_CATEGORIES below is just what the UI offers as quick-pick
chips/datalist options.
"""

from datetime import datetime

from flask import request, jsonify
from flask_login import current_user

from extensions import app
from database.db import get_db
from utils.decorators import login_required, admin_required, verified_required
from utils.security import sanitize
from services.year_sheet_service import list_configured_years, is_year_configured


SUGGESTED_CATEGORIES = [
    "News", "Internship", "Hackathon", "Contest",
    "Learning", "Opportunity", "Announcement", "Resource",
]


# ── shared helpers ────────────────────────────────────────────────────────────

def _require_mentor_year():
    """Same contract as routes/admin.py's _require_mentor_year(): reads
    `year` from query string or form/json body, validates it against
    the configured years, and returns (year, None) or (None, error_response).
    Duplicated (not imported) because admin.py's version is a module-private
    helper (`_`-prefixed) — see that file's docstring for why year
    validation always goes through list_configured_years()."""
    body = request.get_json(silent=True) or {}
    year = request.values.get("year") or body.get("year")
    year = sanitize(str(year or ""), 16)
    if not year:
        return None, (jsonify({"success": False, "message": "Select a year first."}), 400)
    if not is_year_configured(year):
        return None, (jsonify({"success": False, "message": f"Year '{year}' isn't configured yet. Set it up in Mentor Mode first."}), 400)
    return year, None


def _row_out(r):
    return {
        "id": r["id"],
        "cohort_year": r["cohort_year"],
        "title": r["title"],
        "description": r["description"] or "",
        "category": r["category"] or "Announcement",
        "external_url": r["external_url"] or "",
        "image_url": r["image_url"] or "",
        "created_by": r.get("created_by"),
        "mentor_name": r.get("mentor_name") or r.get("mentor_username") or "Mentor",
        "created_at": r["created_at"] or "",
        "updated_at": r["updated_at"] or "",
        "published": bool(r["published"]),
        "pinned": bool(r["pinned"]),
    }


# ══════════════════════════════════════════════════════════════════════════
# STUDENT ROUTES — year is ALWAYS current_user.cohort_year. Never trust a
# year/cohort value from query params, form body, or JSON here.
# ══════════════════════════════════════════════════════════════════════════

@app.route("/recommendations")
@login_required
@verified_required
def student_recommendations():
    year = (getattr(current_user, "cohort_year", "") or "").strip()
    if not year:
        return jsonify({
            "success": True,
            "cohort_year": None,
            "recommendations": [],
            "categories": [],
            "message": "You haven't been assigned to a year yet — ask your mentor.",
        })

    category = sanitize(request.args.get("category", ""), 40)
    q = sanitize(request.args.get("q", ""), 120)

    sql = """
        SELECT r.*, u.full_name AS mentor_name, u.username AS mentor_username
        FROM recommendations r
        LEFT JOIN users u ON u.id = r.created_by
        WHERE r.cohort_year = ? AND r.published = 1
    """
    params = [year]
    if category and category.lower() != "all":
        sql += " AND LOWER(r.category) = LOWER(?)"
        params.append(category)
    if q:
        sql += " AND (LOWER(r.title) LIKE LOWER(?) OR LOWER(r.description) LIKE LOWER(?))"
        like = f"%{q}%"
        params.extend([like, like])
    sql += " ORDER BY r.pinned DESC, r.created_at DESC"

    with get_db() as db:
        rows = db.execute(sql, tuple(params)).fetchall()
        cat_rows = db.execute(
            "SELECT DISTINCT category FROM recommendations WHERE cohort_year=? AND published=1",
            (year,)
        ).fetchall()

    return jsonify({
        "success": True,
        "cohort_year": year,
        "recommendations": [_row_out(r) for r in rows],
        "categories": sorted({(c["category"] or "Announcement") for c in cat_rows}),
    })


@app.route("/recommendations/<int:rec_id>")
@login_required
@verified_required
def student_recommendation_detail(rec_id):
    year = (getattr(current_user, "cohort_year", "") or "").strip()
    if not year:
        return jsonify({"success": False, "message": "You haven't been assigned to a year yet."}), 404

    with get_db() as db:
        row = db.execute("""
            SELECT r.*, u.full_name AS mentor_name, u.username AS mentor_username
            FROM recommendations r
            LEFT JOIN users u ON u.id = r.created_by
            WHERE r.id=? AND r.cohort_year=? AND r.published=1
        """, (rec_id, year)).fetchone()

    # Deliberately the same 404 whether the id doesn't exist at all or it
    # belongs to another year — never leak which case it was.
    if not row:
        return jsonify({"success": False, "message": "Recommendation not found."}), 404

    return jsonify({"success": True, "recommendation": _row_out(row)})


# ══════════════════════════════════════════════════════════════════════════
# MENTOR ROUTES — admin-only, one year at a time (picked in the UI, same
# pattern as routes/admin.py's mentor problem-assignment endpoints).
# ══════════════════════════════════════════════════════════════════════════

@app.route("/admin/recommendations")
@login_required
@admin_required
def admin_recommendations_list():
    year, err = _require_mentor_year()
    if err:
        return err

    category = sanitize(request.args.get("category", ""), 40)
    q = sanitize(request.args.get("q", ""), 120)
    status = request.args.get("status", "all")  # all | published | unpublished

    sql = """
        SELECT r.*, u.full_name AS mentor_name, u.username AS mentor_username
        FROM recommendations r
        LEFT JOIN users u ON u.id = r.created_by
        WHERE r.cohort_year = ?
    """
    params = [year]
    if category and category.lower() != "all":
        sql += " AND LOWER(r.category) = LOWER(?)"
        params.append(category)
    if status == "published":
        sql += " AND r.published = 1"
    elif status == "unpublished":
        sql += " AND r.published = 0"
    if q:
        sql += " AND (LOWER(r.title) LIKE LOWER(?) OR LOWER(r.description) LIKE LOWER(?))"
        like = f"%{q}%"
        params.extend([like, like])
    sql += " ORDER BY r.pinned DESC, r.created_at DESC"

    with get_db() as db:
        rows = db.execute(sql, tuple(params)).fetchall()

    return jsonify({
        "success": True,
        "year": year,
        "years": list_configured_years(),
        "categories": SUGGESTED_CATEGORIES,
        "recommendations": [_row_out(r) for r in rows],
    })


@app.route("/admin/recommendations", methods=["POST"])
@login_required
@admin_required
def admin_recommendations_create():
    year, err = _require_mentor_year()
    if err:
        return err

    body = request.get_json(silent=True) or request.form

    title = sanitize(body.get("title", ""), 200)
    if not title:
        return jsonify({"success": False, "message": "Title is required."}), 400

    description = sanitize(body.get("description", ""), 2000)
    category = sanitize(body.get("category", "") or "Announcement", 40)
    external_url = sanitize(body.get("external_url", ""), 500)
    image_url = sanitize(body.get("image_url", ""), 500)
    pinned = 1 if str(body.get("pinned", "0")) in ("1", "true", "on") else 0
    published = 0 if str(body.get("published", "1")) in ("0", "false", "off") else 1

    now = datetime.now().isoformat()
    with get_db() as db:
        cur = db.execute("""
            INSERT INTO recommendations
            (cohort_year, title, description, category, external_url, image_url,
             created_by, created_at, updated_at, published, pinned)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            RETURNING id
        """, (year, title, description, category, external_url, image_url,
              current_user.id, now, now, published, pinned))
        new_id = cur.fetchone()["id"]
        db.commit()

    return jsonify({"success": True, "message": f"Recommendation created for {year}.", "id": new_id})


@app.route("/admin/recommendations/<int:rec_id>/edit", methods=["POST"])
@login_required
@admin_required
def admin_recommendations_edit(rec_id):
    body = request.get_json(silent=True) or request.form

    with get_db() as db:
        existing = db.execute("SELECT * FROM recommendations WHERE id=?", (rec_id,)).fetchone()
        if not existing:
            return jsonify({"success": False, "message": "Recommendation not found."}), 404

        title = sanitize(body.get("title", existing["title"]), 200)
        if not title:
            return jsonify({"success": False, "message": "Title is required."}), 400
        description = sanitize(body.get("description", existing["description"] or ""), 2000)
        category = sanitize(body.get("category", existing["category"] or "Announcement"), 40)
        external_url = sanitize(body.get("external_url", existing["external_url"] or ""), 500)
        image_url = sanitize(body.get("image_url", existing["image_url"] or ""), 500)

        db.execute("""
            UPDATE recommendations
            SET title=?, description=?, category=?, external_url=?, image_url=?, updated_at=?
            WHERE id=?
        """, (title, description, category, external_url, image_url,
              datetime.now().isoformat(), rec_id))
        db.commit()

    return jsonify({"success": True, "message": "Recommendation updated."})


@app.route("/admin/recommendations/<int:rec_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_recommendations_delete(rec_id):
    with get_db() as db:
        existing = db.execute("SELECT id FROM recommendations WHERE id=?", (rec_id,)).fetchone()
        if not existing:
            return jsonify({"success": False, "message": "Recommendation not found."}), 404
        db.execute("DELETE FROM recommendations WHERE id=?", (rec_id,))
        db.commit()
    return jsonify({"success": True, "message": "Recommendation deleted."})


@app.route("/admin/recommendations/<int:rec_id>/toggle_publish", methods=["POST"])
@login_required
@admin_required
def admin_recommendations_toggle_publish(rec_id):
    with get_db() as db:
        row = db.execute("SELECT published FROM recommendations WHERE id=?", (rec_id,)).fetchone()
        if not row:
            return jsonify({"success": False, "message": "Recommendation not found."}), 404
        new_val = 0 if row["published"] else 1
        db.execute("UPDATE recommendations SET published=?, updated_at=? WHERE id=?",
                    (new_val, datetime.now().isoformat(), rec_id))
        db.commit()
    return jsonify({"success": True, "published": bool(new_val),
                     "message": "Published." if new_val else "Unpublished."})


@app.route("/admin/recommendations/<int:rec_id>/toggle_pin", methods=["POST"])
@login_required
@admin_required
def admin_recommendations_toggle_pin(rec_id):
    with get_db() as db:
        row = db.execute("SELECT pinned FROM recommendations WHERE id=?", (rec_id,)).fetchone()
        if not row:
            return jsonify({"success": False, "message": "Recommendation not found."}), 404
        new_val = 0 if row["pinned"] else 1
        db.execute("UPDATE recommendations SET pinned=?, updated_at=? WHERE id=?",
                    (new_val, datetime.now().isoformat(), rec_id))
        db.commit()
    return jsonify({"success": True, "pinned": bool(new_val),
                     "message": "Pinned." if new_val else "Unpinned."})
