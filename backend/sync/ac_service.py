"""
sync/ac_service.py
AtCoder fetch logic — incremental sync support
"""
import requests, time
from datetime import datetime


def fetch_ac(ac_handle, last_sync=None):
    """Fetch accepted AC submissions. Incremental if last_sync provided."""
    if not ac_handle:
        return []

    rows = []
    seen = set()

    try:
        r = requests.get(
            f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={ac_handle}&from_second=0",
            timeout=20
        )
        data = r.json()
        if not isinstance(data, list):
            return []

        # Fetch problem map for titles
        problem_map = {}
        try:
            pr = requests.get("https://kenkoooo.com/atcoder/resources/problems.json", timeout=20)
            for p in pr.json():
                pid = p.get("id")
                if pid:
                    problem_map[pid] = p.get("title", pid)
        except Exception:
            pass

        for sub in data:
            if sub.get("result") != "AC":
                continue

            pid = sub.get("problem_id")
            if not pid or pid in seen:
                continue
            seen.add(pid)

            ts   = sub.get("epoch_second", int(time.time()))
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

            contest = sub.get("contest_id", "")
            title   = problem_map.get(pid, pid)
            link    = f"https://atcoder.jp/contests/{contest}/tasks/{pid}"

            suffix = pid.split("_")[-1].lower() if "_" in pid else ""
            if suffix == "a":
                diff, topic = "Easy", "Implementation"
            elif suffix == "b":
                diff, topic = "Easy", "Math"
            elif suffix == "c":
                diff, topic = "Medium", "Greedy"
            elif suffix == "d":
                diff, topic = "Hard", "Dynamic Programming"
            else:
                diff, topic = "Unknown", "General"

            rows.append({
                "platform": "AtCoder",
                "problem_name": title,
                "problem_id": pid,
                "problem_url": link,
                "difficulty": diff,
                "tags": topic,
                "solved_date": date,
            })

    except Exception as e:
        print(f"[AC] Error: {e}")

    print(f"[AC] Fetched {len(rows)} problems")
    return rows
