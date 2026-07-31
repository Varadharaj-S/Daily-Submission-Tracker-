"""
sync/lc_service.py
LeetCode fetch logic — cookie-based full import + public fallback
"""
import requests, time, json, os
from datetime import datetime

LC_GQL  = "https://leetcode.com/graphql"
LC_CACHE_FILE = "lc_cache.json"

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://leetcode.com",
    "Referer": "https://leetcode.com/",
}

def load_cache(path=LC_CACHE_FILE):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_cache(data, path=LC_CACHE_FILE):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def get_lc_details(slug):
    try:
        res = requests.post(
            LC_GQL,
            json={
                "query": """
                query($titleSlug: String!) {
                  question(titleSlug: $titleSlug) {
                    difficulty
                    topicTags { name }
                  }
                }
                """,
                "variables": {"titleSlug": slug},
            },
            headers={"Content-Type": "application/json", "Referer": "https://leetcode.com"},
            timeout=60,
        )
        q = res.json().get("data", {}).get("question") or {}
        diff = q.get("difficulty", "Unknown")
        tags = q.get("topicTags") or []
        topic = ", ".join(t["name"] for t in tags if t.get("name")) or "General"
        return diff, topic
    except Exception:
        return "Unknown", "General"

def parse_timestamp(ts):
    if ts is None or ts == "":
        return datetime.now().strftime("%Y-%m-%d")
    try:
        if isinstance(ts, str) and ts.isdigit():
            ts = int(ts)
        if isinstance(ts, (int, float)):
            if ts > 10**12:
                ts = ts / 1000
            return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        pass
    return datetime.now().strftime("%Y-%m-%d")

def _normalize_items(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "acSubmission", "submission", "submissions", "result"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                for inner in ("data", "items", "list", "rows"):
                    v = val.get(inner)
                    if isinstance(v, list):
                        return v
    return []

def fetch_lc_public(lc_handle, last_sync=None):
    """Public API fetch — last ~20 submissions. Uses incremental if last_sync provided."""
    if not lc_handle:
        return []

    rows = []
    seen = set()

    endpoints = [
        f"https://alfa-leetcode-api.onrender.com/{lc_handle}/acSubmission?limit=20",
        f"https://alfa-leetcode-api.onrender.com/{lc_handle}/submission?limit=20",
    ]

    for endpoint in endpoints:
        try:
            res = requests.get(endpoint, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if res.status_code != 200:
                continue
            data = res.json()
            subs = _normalize_items(data)
            if not subs:
                continue

            temp = []
            for sub in subs:
                slug = (sub.get("titleSlug") or sub.get("title_slug") or
                        sub.get("questionTitleSlug") or "")
                title = (sub.get("title") or sub.get("questionTitle") or slug or "Unknown")
                if not slug or slug in seen:
                    continue
                seen.add(slug)

                status = str(sub.get("statusDisplay") or sub.get("status") or "").lower()
                if status and not any(k in status for k in ("accepted", "ac", "success")):
                    continue

                ts = (sub.get("timestamp") or sub.get("submittedAt") or
                      sub.get("date") or int(time.time()))
                date = parse_timestamp(ts)

                # Incremental: skip if older than last_sync
                if last_sync:
                    try:
                        sub_dt = datetime.strptime(date, "%Y-%m-%d")
                        last_dt = datetime.strptime(last_sync[:10], "%Y-%m-%d")
                        if sub_dt < last_dt:
                            continue
                    except Exception:
                        pass

                sub_id = sub.get("id") or sub.get("submissionId") or ""
                problem_link = f"https://leetcode.com/problems/{slug}/"
                diff, topic = get_lc_details(slug)

                temp.append({
                    "platform": "LeetCode",
                    "problem_name": title,
                    "problem_id": slug,
                    "problem_url": problem_link,
                    "difficulty": diff,
                    "tags": topic,
                    "solved_date": date,
                })

            if temp:
                rows = temp
                break
        except Exception as e:
            print(f"[LC] endpoint error: {e}")

    if rows:
        save_cache(rows)
        return rows

    cached = load_cache()
    if cached:
        print(f"[LC] Using cache ({len(cached)} rows)")
        return cached
    return []

def fetch_lc_cookie(lc_handle, session_cookie, last_sync=None):
    """Full import using LEETCODE_SESSION cookie — paginated REST API."""
    if not lc_handle or not session_cookie:
        return fetch_lc_public(lc_handle, last_sync)

    sess = requests.Session()
    sess.headers.update(BROWSER_HEADERS)
    sess.cookies.set("LEETCODE_SESSION", session_cookie, domain=".leetcode.com")

    # Get CSRF token
    try:
        csrf_res = sess.get("https://leetcode.com/", timeout=60)
        csrf = csrf_res.cookies.get("csrftoken", "")
        if csrf:
            sess.headers["x-csrftoken"] = csrf
            sess.cookies.set("csrftoken", csrf, domain=".leetcode.com")
    except Exception:
        pass

    all_subs = []
    seen = set()
    offset = 0
    limit = 40

    while True:
        try:
            r = sess.get(
                f"https://leetcode.com/api/submissions/?offset={offset}&limit={limit}",
                timeout=15
            )
            if r.status_code in (401, 403):
                # Cookie expired — caller should mark user's cookie as expired
                raise PermissionError(f"LC cookie expired (HTTP {r.status_code})")
            if r.status_code != 200:
                break
            data = r.json()
            submissions = data.get("submissions_dump", [])
            if not submissions:
                break

            for sub in submissions:
                if sub.get("status_display") != "Accepted":
                    continue
                slug = sub.get("title_slug", "")
                if not slug or slug in seen:
                    continue
                seen.add(slug)

                ts = sub.get("timestamp", int(time.time()))
                date = parse_timestamp(ts)

                # Incremental: stop fetching if older than last_sync
                if last_sync:
                    try:
                        sub_dt = datetime.strptime(date, "%Y-%m-%d")
                        last_dt = datetime.strptime(last_sync[:10], "%Y-%m-%d")
                        if sub_dt < last_dt:
                            # Save what we have and stop
                            save_cache(all_subs)
                            return all_subs
                    except Exception:
                        pass

                diff, topic = get_lc_details(slug)
                all_subs.append({
                    "platform": "LeetCode",
                    "problem_name": sub.get("title", slug),
                    "problem_id": slug,
                    "problem_url": f"https://leetcode.com/problems/{slug}/",
                    "difficulty": diff,
                    "tags": topic,
                    "solved_date": date,
                })
                time.sleep(0.1)  # Rate-limit GraphQL calls

            if not data.get("has_next"):
                break
            offset += limit
            time.sleep(0.5)
        except Exception as e:
            print(f"[LC Cookie] Error: {e}")
            break

    if all_subs:
        save_cache(all_subs)
        return all_subs

    # Fallback to public
    return fetch_lc_public(lc_handle, last_sync)
