"""
services/incremental_sync/fetchers.py — per-platform fetch, filtered to
"since last sync" where the platform's API actually supports it.

Deliberately reuses the exact same endpoints/params as normal_sync.py's
fetch_cf/fetch_lc/fetch_ac (which are the already-working, in-production
calls) — the only change is adding the incremental filtering. This file
returns clean dicts, not the HYPERLINK-formula sheet rows normal_sync.py
builds; converting to a sheet row is sheet_ops.py's job, not the fetcher's.

Per-platform incremental reality (this is not uniform — being upfront
about what's actually possible per platform rather than pretending all
three work the same way):

  Codeforces — real incremental fetch. `user.status` has no date filter,
  but returns newest-submission-first, so a small `count` plus an early
  stop once we cross `since` is a genuine reduction from "download
  everything" to "download roughly what's new".

  AtCoder — real incremental fetch. The kenkoooo mirror's
  `/v3/user/submissions` endpoint has a documented `from_second` param
  that does actual server-side filtering — the best case of the three.

  LeetCode — NOT incremental in any meaningful sense. There's no official
  API and the third-party mirror this app already depends on
  (alfa-leetcode-api) only exposes "most recent N accepted submissions",
  with no since/cursor param at all. `fetch_lc_incremental` fetches the
  same fixed recent window normal_sync.py's fetch_lc already does — this
  is not a regression, it's the ceiling of what's possible against that
  API. Dedup against the DB (db_ops.insert_new_submissions) is what
  prevents re-processing, same as production does today.
"""

import time
from datetime import datetime, timezone

import requests

from normal_sync import safe_get_json, get_lc_details, _normalize_lc_items, parse_timestamp


def _to_epoch(dt):
    if dt is None:
        return 0
    if isinstance(dt, (int, float)):
        return int(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# ── Codeforces ──────────────────────────────────────────────────────────────
def fetch_cf_incremental(cf_handle, since=None, max_count=500):
    """Returns submissions since `since` (datetime or epoch seconds, or None
    for full history — used on first import). Stops paging once a
    submission older than `since` is seen, since CF returns newest-first."""
    if not cf_handle:
        return []

    since_epoch = _to_epoch(since)
    # First import (since=None) still needs a ceiling — full history via a
    # single large count, same as normal_sync.py's fetch_cf does today.
    count = max_count if since_epoch else 10000

    url = f"https://codeforces.com/api/user.status?handle={cf_handle}&count={count}"
    data, err = safe_get_json(url, timeout=20)
    if not data or data.get("status") != "OK":
        print(f"[CF-incremental ERROR] {err or 'bad response'}")
        return []

    items = []
    seen = set()
    for sub in data.get("result", []):
        if sub.get("verdict") != "OK":
            continue
        creation_ts = sub.get("creationTimeSeconds", 0)
        if since_epoch and creation_ts <= since_epoch:
            break  # newest-first: everything after this is even older

        prob = sub.get("problem", {})
        contest_id = prob.get("contestId")
        index = prob.get("index")
        if not contest_id or not index:
            continue
        pid = f"{contest_id}-{index}"
        if pid in seen:
            continue
        seen.add(pid)

        rating = prob.get("rating")
        difficulty = "Medium" if rating is None else (
            "Easy" if rating < 1200 else "Medium" if rating <= 1800 else "Hard"
        )

        items.append({
            "platform": "Codeforces",
            "problem_id": pid,
            "problem_name": prob.get("name", "Unknown"),
            "problem_url": f"https://codeforces.com/problemset/problem/{contest_id}/{index}",
            "submission_url": f"https://codeforces.com/contest/{contest_id}/submission/{sub.get('id', '')}",
            "difficulty": difficulty,
            "tags": ", ".join(prob.get("tags", [])) or "General",
            "solved_date": datetime.fromtimestamp(creation_ts).strftime("%d-%m-%Y"),
            "solved_epoch": creation_ts,
        })

    if since_epoch and len(data.get("result", [])) >= count:
        # We may have hit the count ceiling before reaching `since` (a user
        # who solved more than max_count problems since last sync — rare,
        # but don't silently under-report; caller can log/retry with a
        # larger count if this flag matters to them).
        print(f"[CF-incremental WARNING] hit count={count} ceiling for {cf_handle}; "
              f"there may be more unfetched submissions since last sync.")

    return items


# ── LeetCode (see module docstring — no real incremental support exists) ─────
def fetch_lc_incremental(lc_handle, since=None):
    if not lc_handle:
        return []

    endpoints = [
        f"https://alfa-leetcode-api.onrender.com/{lc_handle}/acSubmission?limit=20",
        f"https://alfa-leetcode-api.onrender.com/{lc_handle}/submission?limit=20",
    ]

    items = []
    seen = set()
    for endpoint in endpoints:
        try:
            data, err = safe_get_json(endpoint, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if not data:
                continue
            subs = _normalize_lc_items(data)
            if not subs:
                continue

            for sub in subs:
                slug = sub.get("titleSlug") or sub.get("title_slug") or sub.get("questionTitleSlug") or ""
                if not slug or slug in seen:
                    continue
                status_text = str(
                    sub.get("statusDisplay") or sub.get("status_display")
                    or sub.get("status") or sub.get("state") or ""
                ).lower()
                if status_text and not any(k in status_text for k in ("accepted", "ac", "success")):
                    continue
                seen.add(slug)

                ts = sub.get("timestamp") or sub.get("submittedAt") or sub.get("date") or sub.get("createdAt") or int(time.time())
                date_str = parse_timestamp(ts)  # "%Y-%m-%d"
                title = sub.get("title") or sub.get("questionTitle") or slug
                sub_id = sub.get("id") or sub.get("submissionId") or sub.get("submission_id") or ""
                diff, topic = get_lc_details(slug)

                items.append({
                    "platform": "LeetCode",
                    "problem_id": slug,
                    "problem_name": title,
                    "problem_url": f"https://leetcode.com/problems/{slug}/",
                    "submission_url": f"https://leetcode.com/submissions/detail/{sub_id}/" if sub_id else f"https://leetcode.com/problems/{slug}/",
                    "difficulty": diff,
                    "tags": topic,
                    "solved_date": datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y"),
                    "solved_epoch": int(datetime.strptime(date_str, "%Y-%m-%d").timestamp()),
                })

            if items:
                break
        except Exception as e:
            print(f"[LC-incremental] endpoint exception: {e}")

    return items


# ── AtCoder ─────────────────────────────────────────────────────────────────
def fetch_ac_incremental(ac_handle, since=None):
    """Real incremental fetch via kenkoooo's documented from_second param."""
    if not ac_handle:
        return []

    since_epoch = _to_epoch(since)
    data, err = safe_get_json(
        f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions"
        f"?user={ac_handle}&from_second={since_epoch}",
        timeout=20,
    )
    if not data or not isinstance(data, list):
        print(f"[AC-incremental ERROR] {err or 'bad response'}")
        return []

    problem_data, _ = safe_get_json("https://kenkoooo.com/atcoder/resources/problems.json", timeout=20)
    problem_map = {}
    if isinstance(problem_data, list):
        for p in problem_data:
            if p.get("id") and p.get("title"):
                problem_map[p["id"]] = p["title"]

    items = []
    seen = set()
    for sub in data:
        if sub.get("result") != "AC":
            continue
        pid = sub.get("problem_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)

        contest = sub.get("contest_id", "")
        epoch = sub.get("epoch_second", 0)

        if "_a" in pid:
            diff, topic = "Easy", "Implementation"
        elif "_b" in pid:
            diff, topic = "Easy", "Math"
        elif "_c" in pid:
            diff, topic = "Medium", "Greedy"
        elif "_d" in pid:
            diff, topic = "Hard", "Dynamic Programming"
        else:
            diff, topic = "Unknown", "General"

        items.append({
            "platform": "AtCoder",
            "problem_id": pid,
            "problem_name": problem_map.get(pid, pid),
            "problem_url": f"https://atcoder.jp/contests/{contest}/tasks/{pid}",
            "submission_url": f"https://atcoder.jp/contests/{contest}/submissions/{sub.get('id', '')}",
            "difficulty": diff,
            "tags": topic,
            "solved_date": datetime.fromtimestamp(epoch).strftime("%d-%m-%Y"),
            "solved_epoch": epoch,
        })

    return items
