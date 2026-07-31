"""
sync/cf_service.py
Codeforces fetch logic — incremental sync support
"""
import requests, time
from datetime import datetime


def fetch_cf(cf_handle, last_sync=None):
    """Fetch accepted CF submissions. Incremental if last_sync provided."""
    if not cf_handle:
        return []

    rows = []
    seen = set()

    try:
        r = requests.get(
            f"https://codeforces.com/api/user.status?handle={cf_handle}&count=10000",
            timeout=20
        )
        data = r.json()
        if data.get("status") != "OK":
            print(f"[CF] API error: {data.get('comment', 'unknown')}")
            return []

        for sub in data.get("result", []):
            if sub.get("verdict") != "OK":
                continue

            prob = sub.get("problem", {})
            cid  = str(prob.get("contestId", ""))
            idx  = prob.get("index", "")
            pid  = f"{cid}-{idx}"

            if not cid or not idx or pid in seen:
                continue
            seen.add(pid)

            ts   = sub.get("creationTimeSeconds", int(time.time()))
            date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

            # Incremental: skip old
            if last_sync:
                try:
                    sub_dt  = datetime.strptime(date, "%Y-%m-%d")
                    last_dt = datetime.strptime(last_sync[:10], "%Y-%m-%d")
                    if sub_dt < last_dt:
                        continue
                except Exception:
                    pass

            rating = prob.get("rating") or 0
            if rating < 1200:
                diff = "Easy"
            elif rating <= 1800:
                diff = "Medium"
            else:
                diff = "Hard"

            topic = ", ".join(prob.get("tags", [])) or "General"
            link  = f"https://codeforces.com/problemset/problem/{cid}/{idx}"
            title = prob.get("name", pid)

            rows.append({
                "platform": "Codeforces",
                "problem_name": title,
                "problem_id": pid,
                "problem_url": link,
                "difficulty": diff,
                "tags": topic,
                "solved_date": date,
            })

    except Exception as e:
        print(f"[CF] Error: {e}")

    print(f"[CF] Fetched {len(rows)} problems")
    return rows
