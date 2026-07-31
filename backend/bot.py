"""
bot.py v4 — FIXED LeetCode + Google Sheets
══════════════════════════════════════════════════════════════════════════════

LEETCODE FIX:
  Strategy 1 (BEST):  Browser Session Cookie — user pastes LEETCODE_SESSION
                       from DevTools. No login, no captcha, full history.
  Strategy 2:         Internal mobile API login — works sometimes
  Strategy 3:         Public recentAcSubmissionList — always works, last 20 only

GOOGLE SHEETS FIX:
  Requires service_account.json — see GOOGLE_SHEETS_SETUP.md
  Column format: DATE | PROGRAM TITLE | LINK | DIFFICULTY | PLATFORM | TOPIC | COUNT
"""

import requests, time, json, re, os
import pandas as pd
from datetime import datetime, timedelta
from collections import Counter

LC_GQL  = "https://leetcode.com/graphql"
LC_API  = "https://leetcode.com/api/session/"
LC_BASE = "https://leetcode.com"

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":  LC_BASE,
    "Referer": LC_BASE + "/",
}



ATCODER_PROBLEM_MAP = {}

# ── URL builders ──────────────────────────────────────────────────────────────
def cf_url(cid, idx):
    return f"https://codeforces.com/contest/{cid}/problem/{idx}" if cid and idx else ""
def lc_url(slug):
    return f"https://leetcode.com/problems/{slug}/" if slug else ""
def ac_url(pid):
    parts = pid.split("_")
    if len(parts) >= 2:
        return f"https://atcoder.jp/contests/{'_'.join(parts[:-1])}/tasks/{pid}"
    return f"https://atcoder.jp/tasks/{pid}"

# ══════════════════════════════════════════════════════════════════════════════
# CODEFORCES
# ══════════════════════════════════════════════════════════════════════════════
def fetch_codeforces(handle):
    if not handle: return []
    try:
        r    = requests.get(
            f"https://codeforces.com/api/user.status?handle={handle}&from=1&count=10000",
            timeout=15)
        data = r.json()
        if data.get("status") != "OK": return []
        seen, results = set(), []
        for sub in data.get("result", []):
            if sub.get("verdict") != "OK": continue
            prob = sub.get("problem", {})
            cid  = str(prob.get("contestId", ""))
            idx  = prob.get("index", "")
            pid  = f"{cid}{idx}"
            if pid in seen: continue
            seen.add(pid)
            ts     = sub.get("creationTimeSeconds", 0)
            rating = prob.get("rating", 0) or 0
            diff   = "Easy" if rating < 1400 else ("Medium" if rating < 2000 else "Hard")
            results.append({
    "platform": "Codeforces",
    "problem_name": prob.get("name", pid),
    "problem_id": pid,
    "problem_url": cf_url(cid, idx),
    "submission_url": f"https://codeforces.com/contest/{cid}/submission/{sub.get('id')}",
    "difficulty": diff,
    "tags": ";".join(prob.get("tags", [])),
    "solved_date": datetime.utcfromtimestamp(ts).strftime("%d-%m-%Y"),
})
        return results
    except Exception as e:
        print(f"[CF Error] {e}"); return []

# ══════════════════════════════════════════════════════════════════════════════
# LEETCODE  — 3-strategy approach
# ══════════════════════════════════════════════════════════════════════════════
class LeetCodeFetcher:
    """
    Fetches LeetCode submissions using LEETCODE_SESSION cookie.
    Uses the /api/submissions/ REST endpoint — works reliably when cookie is valid.
    Falls back to public GraphQL API (last 20) if no cookie.
    """

    def __init__(self, cookie_val=""):
        self.sess = requests.Session()
        self.sess.headers.update(BROWSER_HEADERS)
        self.cookie_val = ""
        self.logged_in = False
        if cookie_val:
            self._set_cookie(cookie_val)

    def _set_cookie(self, cookie_val, csrf_token=""):
        """Extract and set LEETCODE_SESSION cookie. Handles full cookie string or bare value."""
        if not cookie_val or len(cookie_val) < 10:
            return False
        # If user pasted the full cookie header string, extract just the session value
        if "LEETCODE_SESSION=" in cookie_val:
            m = re.search(r"LEETCODE_SESSION=([^;]+)", cookie_val)
            if m:
                cookie_val = m.group(1)
        cookie_val = cookie_val.strip()
        self.cookie_val = cookie_val
        # Set LEETCODE_SESSION cookie
        self.sess.cookies.set("LEETCODE_SESSION", cookie_val, domain=".leetcode.com", path="/")

        # If user provided csrf token directly — use it
        if csrf_token and len(csrf_token) > 5:
            csrf = csrf_token.strip()
            self.sess.cookies.set("csrftoken", csrf, domain=".leetcode.com", path="/")
        else:
            # Get csrftoken by hitting the homepage once
            try:
                self.sess.get("https://leetcode.com/", timeout=0)
                csrf = self.sess.cookies.get("csrftoken", "")
            except Exception as e:
                print(f"[LC _set_cookie] {e}")
                csrf = ""
        self.sess.headers.update({
            "Referer": "https://leetcode.com/",
            "Origin": "https://leetcode.com",
            "X-Requested-With": "XMLHttpRequest"
        })
        self.sess.get("https://leetcode.com/", timeout=60)
        return True
    
    def use_cookie(self, cookie_val, csrf_token=""):
        """Set cookie and verify it works by calling userStatus."""
        if not self._set_cookie(cookie_val, csrf_token):
            return False
        try:
            csrf = self.sess.cookies.get("csrftoken", "")

            self.sess.headers.update({
                "Referer": "https://leetcode.com/",
                "Origin": "https://leetcode.com",
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRFToken": csrf
            })

            r = self.sess.post(
                    LC_GQL,
                    json={"query": "query{userStatus{username isSignedIn}}"},
                    timeout=60
                    )
            print("CSRF FROM COOKIE:", self.sess.cookies.get("csrftoken"))
            d = r.json()
            if (d.get("data") or {}).get("userStatus", {}).get("isSignedIn"):
                self.logged_in = True
                print(f"[LC] Cookie login OK")
                return True
        except Exception as e:
            print(f"[LC Cookie verify error] {e}")
        return False
        
    def fetch_all_via_api(self, username):
        csrf = self.sess.cookies.get("csrftoken", "")
        self.sess.headers.update({
            "Referer": "https://leetcode.com/",
            "Origin": "https://leetcode.com",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrf
        })
        print("Cookie:", f"LEETCODE_SESSION={self.cookie_val}; csrftoken={self.sess.cookies.get('csrftoken','')}")

        """
        Fetch ALL accepted submissions using /api/submissions/ REST endpoint.
        This is the most reliable method — works with LEETCODE_SESSION cookie.
        Paginates through all pages (offset 0, 20, 40, ...).
        """

        r = self.sess.post(
    "https://leetcode.com/graphql",
    json={"query": "query{userStatus{username isSignedIn}}"},
    timeout=60
)

        print("LOGIN CHECK:", r.json())
        results = []
        seen = set()
        offset = 0
        url = "https://leetcode.com/api/submissions/"

        # Ensure cookie header is set properly for REST API
        self.sess.headers.update({
            "Referer": "https://leetcode.com/",
            "Origin": "https://leetcode.com",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrf,
        })
        # Set cookie as header too (some LeetCode endpoints need this)
        while True:
            try:
                r = self.sess.get(
                    url,
                    params={"offset": offset, "limit": 20},
                    timeout=20
                )
                if r.status_code == 403:
                    print(f"[LC API] 403 at offset {offset} — cookie may be expired")
                    break
                if r.status_code != 200:
                    print(f"[LC API] Status {r.status_code} at offset {offset}")
                    break

                data = r.json()
                subs = data.get("submissions_dump", [])

                if not subs:
                    break

                new_this_page = 0
                for sub in subs:
                    # Only accepted submissions
                    if sub.get("status_display") != "Accepted":
                        continue
                    slug = sub.get("title_slug", "")
                    if not slug or slug in seen:
                        continue
                    seen.add(slug)
                    ts = int(sub.get("timestamp", 0))
                    new_this_page += 1
                    results.append({
                        "platform": "LeetCode",
                        "problem_name": sub.get("title", slug),
                        "problem_id": slug,
                        "problem_url": f"https://leetcode.com/problems/{slug}/",
                        "submission_url": f"https://leetcode.com/submissions/detail/{sub.get('id', '')}/",
                        "difficulty": "",
                        "tags": "",
                        "solved_date": datetime.utcfromtimestamp(ts).strftime("%d-%m-%Y"),
                    })

                # has_next tells us if more pages exist
                if not data.get("has_next", False):
                    break

                offset += 20
                time.sleep(0.3)  # polite delay

            except Exception as e:
                print(f"[LC API offset={offset}] {e}")
                break

        print(f"[LC] fetch_all_via_api: got {len(results)} accepted unique submissions")
        # Enrich top 100 with difficulty + tags
        if results:
            self._enrich(results[:100])
        return results

    def fetch_public(self, username):
        """Public API — no login needed, but returns last 20 only."""
        try:
            q = """query($u:String!,$l:Int!){
              recentAcSubmissionList(username:$u,limit:$l){
                id title titleSlug timestamp
              }
            }"""
            r = requests.post(
                LC_GQL,
                json={"query": q, "variables": {"u": username, "l": 20}},
                headers={**BROWSER_HEADERS, "Content-Type": "application/json"},
                timeout=15
            )
            raw = (r.json().get("data") or {}).get("recentAcSubmissionList") or []
            seen, out = set(), []
            for s in raw:
                slug = s.get("titleSlug", "")
                if slug in seen:
                    continue
                seen.add(slug)
                ts = int(s.get("timestamp", 0))
                out.append({
                    "platform": "LeetCode",
                    "problem_name": s.get("title", slug),
                    "problem_id": slug,
                    "problem_url": lc_url(slug),
                    "difficulty": "Medium",
                    "tags": "",
                    "solved_date": datetime.utcfromtimestamp(ts).strftime("%d-%m-%Y"),
                })
            return out
        except Exception as e:
            print(f"[LC Public] {e}")
            return []

    def _enrich(self, items):
        """Add difficulty + tags for items using GraphQL (uses session if available)."""
        for item in items:
            try:
                r = self.sess.post(
                    LC_GQL,
                    json={
                        "query": "query q($s:String!){question(titleSlug:$s){difficulty topicTags{name}}}",
                        "variables": {"s": item["problem_id"]}
                    },
                    timeout=8
                )
                qd = (r.json().get("data") or {}).get("question") or {}
                item["difficulty"] = qd.get("difficulty", "Medium")
                item["tags"] = ";".join(t["name"] for t in qd.get("topicTags", []))
                time.sleep(0.2)
            except:
                item.setdefault("difficulty", "Medium")


def fetch_lc_with_cookie(username, cookie_val, csrf_token=""):
    """
    Standalone function: fetch ALL LeetCode submissions using session cookie.
    Called from /import_lc route for first-time full import.
    Returns list of submission dicts.
    """
    fetcher = LeetCodeFetcher()
    if not fetcher.use_cookie(cookie_val, csrf_token):
        return [], "❌ Cookie login failed — cookie may be expired. Please update in Settings."

    results = fetcher.fetch_all_via_api(username)
    if results:
        return results, f"✅ Fetched {len(results)} accepted submissions via cookie"
    return [], "❌ Cookie fetch failed — cookie may be expired. Please update in Settings."


# ══════════════════════════════════════════════════════════════════════════════
# ATCODER
# ══════════════════════════════════════════════════════════════════════════════

ATCODER_PROBLEM_MAP = {}
ATCODER_TAG_MAP = {}

def load_atcoder_data():
    global ATCODER_PROBLEM_MAP, ATCODER_TAG_MAP

    if ATCODER_PROBLEM_MAP and ATCODER_TAG_MAP:
        return ATCODER_PROBLEM_MAP, ATCODER_TAG_MAP

    try:
        # Load problem names
        problems = requests.get(
            "https://kenkoooo.com/atcoder/resources/problems.json",
            timeout=60
        ).json()

        for p in problems:
            ATCODER_PROBLEM_MAP[p["id"]] = p["title"]

        # Load difficulty
        models = requests.get(
            "https://kenkoooo.com/atcoder/resources/problem-models.json",
            timeout=60
        ).json()

        for pid, val in models.items():
            diff = val.get("difficulty", 0)

            if diff < 800:
                ATCODER_TAG_MAP[pid] = "Implementation"
            elif diff < 1400:
                ATCODER_TAG_MAP[pid] = "Greedy"
            elif diff < 2000:
                ATCODER_TAG_MAP[pid] = "DP"
            else:
                ATCODER_TAG_MAP[pid] = "Advanced"

    except Exception as e:
        print("[AtCoder Load Error]", e)

    return ATCODER_PROBLEM_MAP, ATCODER_TAG_MAP

def load_atcoder_problems():
    global ATCODER_PROBLEM_MAP
    if ATCODER_PROBLEM_MAP:
        return ATCODER_PROBLEM_MAP

    try:
        url = "https://kenkoooo.com/atcoder/resources/problems.json"
        data = requests.get(url, timeout=60).json()

        for p in data:
            ATCODER_PROBLEM_MAP[p["id"]] = p["title"]

    except Exception as e:
        print("[AtCoder Problem Load Error]", e)

    return ATCODER_PROBLEM_MAP

def fetch_atcoder(handle):
    problem_map, tag_map = load_atcoder_data()
    if not handle: return []
    try:
        r    = requests.get(
            f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions"
            f"?user={handle}&from_second=0",
            timeout=20, headers={"User-Agent": "DSATracker/4.0"})
        subs = r.json()
        seen, out = set(), []
        problem_map, tag_map = load_atcoder_data()
        for sub in subs:
            if sub.get("result") != "AC": continue
            pid = sub.get("problem_id","")
            if pid in seen: continue
            seen.add(pid)
            ts   = sub.get("epoch_second", 0)
            pt   = sub.get("point", 0) or 0
            diff = "Easy" if pt<=300 else ("Medium" if pt<=600 else "Hard")
            fallback = "Implementation"
            if "_" in pid:
              level = pid.split("_")[-1].upper()
            if level == "A":
              fallback = "Implementation"
            elif level == "B":
               fallback = "Greedy"
            elif level == "C":
               fallback = "Math"
            elif level == "D":
               fallback = "DP"
            else:
               fallback = "Advanced"
            out.append({
    "platform": "AtCoder",
    "problem_name": problem_map.get(pid, pid),
    "problem_id": pid,
    "problem_url": ac_url(pid),
    "submission_url": f"https://atcoder.jp/contests/{sub.get('contest_id')}/submissions/{sub.get('id')}",
    "difficulty": diff,
    "tags": tag_map.get(pid, fallback),   # ✅ FIXED
    "solved_date": datetime.utcfromtimestamp(ts).strftime("%d-%m-%Y"),
})
        return out
    except Exception as e:
        print(f"[AC Error] {e}"); return []


# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS
# Columns: DATE | PROGRAM TITLE | LINK | DIFFICULTY | PLATFORM | TOPIC | COUNT
# ══════════════════════════════════════════════════════════════════════════════
SHEET_HEADERS = [
    "DATE",
    "PROGRAM TITLE",
    "SUBMISSION LINK",
    "DIFFICULTY",
    "PLATFORM",
    "TOPIC",
    "COUNT"
]

def _gc():
    import gspread
    from google.oauth2.service_account import Credentials
    path = os.environ.get("GOOGLE_CREDS_JSON","valiant-splicer-489013-q2-40d3ac23a2d8.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"service_account.json not found at {os.path.abspath(path)}. "
            "Follow GOOGLE_SHEETS_SETUP.md to create it.")
    import json

    if os.getenv("GOOGLE_SERVICE_JSON"):
        service_info = json.loads(os.environ["GOOGLE_SERVICE_JSON"])
        creds = Credentials.from_service_account_info(
            service_info,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
        )
    else:
        creds = Credentials.from_service_account_file(
            path,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
        )
    return gspread.authorize(creds)

def compute_topic_counts(df):
    c = Counter()
    for s in df["tags"].fillna(""):
        for t in str(s).split(";"):
            t=t.strip()
            if t: c[t]+=1
    return c

def push_to_sheets(sheet_id, df, tab_name="DSA Data", append_only=False):
    if not sheet_id:
        return False, "No Sheet ID."

    try:
        gc = _gc()
        sh = gc.open_by_key(sheet_id)

        try:
            ws = sh.worksheet(tab_name)
        except:
            ws = sh.add_worksheet(title=tab_name, rows=10000, cols=10)

        from collections import defaultdict

        # Build rows from dataframe
        grouped = defaultdict(int)
        for _, r in df.iterrows():
            key = (r["solved_date"], r["problem_name"])
            grouped[key] += 1

        new_rows = []
        added = set()
        for _, row in df.iterrows():
            key = (row["solved_date"], row["problem_name"])
            if key in added:
                continue
            added.add(key)

            title = str(row.get("problem_name", ""))
            url   = str(row.get("problem_url", ""))
            tags  = str(row.get("tags", ""))
            first = next((t.strip() for t in tags.split(",") if t.strip()), "General")

            title_link = f'=HYPERLINK("{url}","{title.replace(chr(34), chr(39))}")' if url else title
            sub_link   = f'=HYPERLINK("{row.get("submission_url","")}","Submission")' if row.get("submission_url") else ""

            new_rows.append([
                row.get("solved_date", ""),
                title_link,
                sub_link,
                row.get("difficulty", ""),
                row.get("platform", ""),
                first,
                grouped[key]
            ])

        if append_only:
            # DAILY SYNC: Just append new rows at bottom — don't touch existing data
            if new_rows:
                ws.append_rows(new_rows, value_input_option="USER_ENTERED")
        else:
            # FIRST TIME or full rewrite: clear and write everything
            ws.clear()
            ws.update([SHEET_HEADERS] + new_rows, value_input_option="USER_ENTERED")

        try:
            ws.format("A1:G1", {"textFormat": {"bold": True}})
            sh.batch_update({
                "requests": [{
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": ws.id,
                            "gridProperties": {"frozenRowCount": 1}
                        },
                        "fields": "gridProperties.frozenRowCount"
                    }
                }]
            })
        except:
            pass

        mode = "appended" if append_only else "updated"
        return True, f"✅ Sheet {mode} — {len(new_rows)} rows in '{tab_name}'."

    except FileNotFoundError as e:
        return False, str(e)

    except Exception as e:
        return False, f"Sheets error: {e}"

def create_user_sheet(email, username):
    try:
        gc = _gc()
        sh = gc.create(f"DSA Tracker — {username}")
        ws = sh.sheet1
        ws.update_title("DSA Data")
        ws.update([SHEET_HEADERS])
        ws.format("A1:G1",{"textFormat":{"bold":True}})

        # Share with the user (writer access)
        if email and "@" in email:
            sh.share(email, perm_type="user", role="writer", notify=True,
                     email_message=f"Hi {username}! Your DSA Tracker sheet is ready.")

        # Share with admin email (writer access, no notification needed)
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        if admin_email and "@" in admin_email and admin_email != email:
            sh.share(admin_email, perm_type="user", role="writer", notify=False)

        # Remove 'anyone with link' access — keep it private to only shared users
        try:
            sh.share("", perm_type="anyone", role="none")
        except Exception:
            pass  # already private by default

        return sh.id, "Sheet created and shared."
    except FileNotFoundError as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN SYNC
# ══════════════════════════════════════════════════════════════════════════════
def sync_user_data(user_row, get_db_fn):
    """
    INCREMENTAL SYNC — fetches only new submissions since last_sync.
    First time (no last_sync): fetches recent 20 via public API.
    For full history: use /import_lc route with cookie.
    Auto-runs every 24 hours via background thread.
    """
    uid        = user_row["id"]
    cf         = (user_row.get("cf_handle")         or "").strip()
    lc         = (user_row.get("lc_handle")         or "").strip()
    lc_cookie  = (user_row.get("lc_session_cookie") or "").strip()
    lc_csrf    = (user_row.get("lc_csrf_token")     or "").strip()
    ac         = (user_row.get("ac_handle")         or "").strip()
    sid        = (user_row.get("sheet_id")          or "").strip()
    uname      = user_row.get("username", "user")
    last_sync  = (user_row.get("last_sync")         or "").strip()

    try:
        enabled = json.loads(user_row.get("enabled_platforms") or
                             '["Codeforces","LeetCode","AtCoder"]')
    except:
        enabled = ["Codeforces", "LeetCode", "AtCoder"]

    if not any([cf, lc, ac]):
        return {"success": False, "message": "No handles configured. Go to Settings."}

    # Parse last_sync datetime for incremental filtering
    last_sync_dt = None
    if last_sync:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                last_sync_dt = datetime.strptime(last_sync, fmt)
                break
            except:
                continue

    all_subs, plat_msgs, warnings = [], [], []

    # ── Codeforces ────────────────────────────────────────────────────────────
    if cf and "Codeforces" in enabled:
        s = fetch_codeforces(cf)
        # Filter: only new submissions since last_sync
        if last_sync_dt and s:
            s = [x for x in s if _parse_solved_date(x["solved_date"]) > last_sync_dt]
        all_subs.extend(s)
        plat_msgs.append(f"Codeforces (+{len(s)})")

    # ── LeetCode ──────────────────────────────────────────────────────────────
    if lc and "LeetCode" in enabled:
        fetcher = LeetCodeFetcher()
        if lc_cookie and len(lc_cookie) > 10:
            # Set cookie + csrf together for reliable auth
            fetcher._set_cookie(lc_cookie, lc_csrf)
            # Cookie available — use REST API for reliable fetch
            raw = fetcher.fetch_all_via_api(lc)
            if raw:
                # Filter incremental
                if last_sync_dt:
                    s = [x for x in raw if _parse_solved_date(x["solved_date"]) > last_sync_dt]
                else:
                    s = raw
                all_subs.extend(s)
                plat_msgs.append(f"LeetCode (+{len(s)})")
            else:
                # Cookie failed — fallback to public
                s = fetcher.fetch_public(lc)
                if last_sync_dt:
                    s = [x for x in s if _parse_solved_date(x["solved_date"]) > last_sync_dt]
                all_subs.extend(s)
                plat_msgs.append(f"LeetCode (+{len(s)}) [public]")
                warnings.append("⚠️ Cookie may be expired. Update in Settings for full data.")
        else:
            # No cookie — use public API (last 20)
            s = fetcher.fetch_public(lc)
            if last_sync_dt:
                s = [x for x in s if _parse_solved_date(x["solved_date"]) > last_sync_dt]
            all_subs.extend(s)
            plat_msgs.append(f"LeetCode (+{len(s)}) [public]")
            warnings.append("⚠️ Add LEETCODE_SESSION cookie in Settings for full history.")

    # ── AtCoder ───────────────────────────────────────────────────────────────
    if ac and "AtCoder" in enabled:
        s = fetch_atcoder(ac)
        if last_sync_dt and s:
            s = [x for x in s if _parse_solved_date(x["solved_date"]) > last_sync_dt]
        all_subs.extend(s)
        plat_msgs.append(f"AtCoder (+{len(s)})")

    # ── Save to DB ────────────────────────────────────────────────────────────
    saved = 0
    if all_subs:
        df = pd.DataFrame(all_subs)
        df.sort_values("solved_date", ascending=False, inplace=True)
        df.reset_index(drop=True, inplace=True)

        db = get_db_fn()
        try:
            for _, row in df.iterrows():
                cur = db.execute("""
                    INSERT INTO submissions
                    (user_id,platform,problem_name,problem_id,
                     problem_url,difficulty,tags,solved_date)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT (user_id, platform, problem_id) DO NOTHING
                """, (uid, row["platform"], row["problem_name"], row["problem_id"],
                      row.get("problem_url", ""), row.get("difficulty", ""),
                      row.get("tags", ""), row["solved_date"]))
                saved += cur.rowcount
            db.commit()
        finally:
            db.close()

        # Push ONLY new rows to sheet (append mode)
        sheets_msg = ""
        if sid:
            _, sheets_msg = push_to_sheets(sid, df, tab_name=uname, append_only=bool(last_sync_dt))
    else:
        sheets_msg = ""
        df = pd.DataFrame()

    total_msg = f"✅ Sync done — {saved} new problems saved ({', '.join(plat_msgs)})."
    if not all_subs:
        total_msg = f"✅ Already up to date — no new submissions since last sync. ({', '.join(plat_msgs) if plat_msgs else 'all platforms checked'})"

    parts = [total_msg]
    if sheets_msg:
        parts.append(sheets_msg)
    if warnings:
        parts.extend(warnings)

    return {
        "success": True,
        "message": " ".join(parts),
        "total": saved,
        "platforms": plat_msgs,
        "warnings": warnings
    }


def import_lc_full(uid, lc_handle, lc_cookie, sheet_id, username, get_db_fn, lc_csrf=""):
    """
    FIRST TIME FULL IMPORT — fetches entire LeetCode submission history using cookie.
    Called from /import_lc route. Saves ALL accepted submissions to DB + sheet.
    """
    if not lc_handle or not lc_cookie:
        return {"success": False, "message": "LeetCode handle and cookie are required."}

    results, msg = fetch_lc_with_cookie(lc_handle, lc_cookie, lc_csrf)

    if not results:
        return {"success": False, "message": msg}

    df = pd.DataFrame(results)
    df.drop_duplicates(subset=["problem_id"], inplace=True)
    df.sort_values("solved_date", ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)

    db = get_db_fn()
    saved = 0
    try:
        for _, row in df.iterrows():
            cur = db.execute("""
                INSERT INTO submissions
                (user_id,platform,problem_name,problem_id,
                 problem_url,difficulty,tags,solved_date)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT (user_id, platform, problem_id) DO NOTHING
            """, (uid, "LeetCode", row["problem_name"], row["problem_id"],
                  row.get("problem_url", ""), row.get("difficulty", ""),
                  row.get("tags", ""), row["solved_date"]))
            saved += cur.rowcount
        db.commit()
    finally:
        db.close()

    # Full sheet rewrite for first-time import
    sheets_msg = ""
    if sheet_id:
        _, sheets_msg = push_to_sheets(sheet_id, df, tab_name=username, append_only=False)

    return {
        "success": True,
        "message": f"✅ Full import done! {saved} new LeetCode problems saved. {sheets_msg}",
        "total": saved
    }


def _parse_solved_date(d):
    """Parse solved_date string to datetime. Handles dd-mm-yyyy and yyyy-mm-dd."""
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(d, fmt)
        except:
            continue
    return datetime.min


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD DATA
# ══════════════════════════════════════════════════════════════════════════════
def get_dashboard_data(subs):
    empty = {
        "total": 0, "cf": 0, "lc": 0, "ac": 0, "streak": 0,
        "recent": [], "chart_labels": [], "chart_data": [],
        "topic_labels": [], "topic_data": [],
        "following_count": 0, "follower_count": 0, "last_sync_msg": None
    }

    if not subs:
        return empty

    rows = [dict(s) for s in subs]
    df = pd.DataFrame(rows)

    # ✅ UNIQUE COUNT (MAIN FIX)
    total = df["problem_id"].nunique()

    cf = df[df["platform"].str.contains("Codeforces", case=False, na=False)]["problem_id"].nunique()
    lc = df[df["platform"].str.contains("LeetCode", case=False, na=False)]["problem_id"].nunique()
    ac = df[df["platform"].str.contains("AtCoder", case=False, na=False)]["problem_id"].nunique()

    # 🔥 STREAK
    dates = sorted(df["solved_date"].dropna().unique(), reverse=True)
    streak, check = 0, datetime.now().date()

    for d in dates:
        try:
            d_obj = datetime.strptime(d, "%d-%m-%Y").date()
        except:
            try:
                d_obj = datetime.strptime(d, "%Y-%m-%d").date()
            except:
                continue

        if d_obj >= check - timedelta(days=1):
            streak += 1
            check = d_obj
        else:
            break

    daily = df.groupby("solved_date").size().reset_index(name="count")
    daily = daily.sort_values("solved_date").tail(30)

    tc = compute_topic_counts(df)
    top8 = tc.most_common(8)

    recent = df.head(10)[[
        "platform", "problem_name", "problem_url",
        "difficulty", "solved_date"
    ]].to_dict("records")

    return {
        "total": total,
        "cf": cf,
        "lc": lc,
        "ac": ac,
        "streak": streak,
        "recent": recent,
        "chart_labels": daily["solved_date"].tolist(),
        "chart_data": daily["count"].tolist(),
        "topic_labels": [t[0] for t in top8],
        "topic_data": [t[1] for t in top8],
        "following_count": 0,
        "follower_count": 0,
        "last_sync_msg": None
    }
# ══════════════════════════════════════════════════════════════════════════════
# DAILY CHALLENGES
# ══════════════════════════════════════════════════════════════════════════════
PROBLEM_POOL={
    "Arrays":[
        {"name":"Two Sum","url":"https://leetcode.com/problems/two-sum/","diff":"Easy","platform":"LeetCode"},
        {"name":"Best Time to Buy and Sell Stock","url":"https://leetcode.com/problems/best-time-to-buy-and-sell-stock/","diff":"Easy","platform":"LeetCode"},
        {"name":"Maximum Subarray","url":"https://leetcode.com/problems/maximum-subarray/","diff":"Medium","platform":"LeetCode"},
        {"name":"Product of Array Except Self","url":"https://leetcode.com/problems/product-of-array-except-self/","diff":"Medium","platform":"LeetCode"},
    ],
    "Strings":[
        {"name":"Valid Anagram","url":"https://leetcode.com/problems/valid-anagram/","diff":"Easy","platform":"LeetCode"},
        {"name":"Longest Substring Without Repeating Characters","url":"https://leetcode.com/problems/longest-substring-without-repeating-characters/","diff":"Medium","platform":"LeetCode"},
        {"name":"Group Anagrams","url":"https://leetcode.com/problems/group-anagrams/","diff":"Medium","platform":"LeetCode"},
    ],
    "Dynamic Programming":[
        {"name":"Climbing Stairs","url":"https://leetcode.com/problems/climbing-stairs/","diff":"Easy","platform":"LeetCode"},
        {"name":"Coin Change","url":"https://leetcode.com/problems/coin-change/","diff":"Medium","platform":"LeetCode"},
        {"name":"Longest Common Subsequence","url":"https://leetcode.com/problems/longest-common-subsequence/","diff":"Medium","platform":"LeetCode"},
        {"name":"Edit Distance","url":"https://leetcode.com/problems/edit-distance/","diff":"Hard","platform":"LeetCode"},
    ],
    "Graphs":[
        {"name":"Number of Islands","url":"https://leetcode.com/problems/number-of-islands/","diff":"Medium","platform":"LeetCode"},
        {"name":"Course Schedule","url":"https://leetcode.com/problems/course-schedule/","diff":"Medium","platform":"LeetCode"},
    ],
    "Math":[
        {"name":"Palindrome Number","url":"https://leetcode.com/problems/palindrome-number/","diff":"Easy","platform":"LeetCode"},
        {"name":"Pow(x, n)","url":"https://leetcode.com/problems/powx-n/","diff":"Medium","platform":"LeetCode"},
    ],
    "Greedy":[
        {"name":"Jump Game","url":"https://leetcode.com/problems/jump-game/","diff":"Medium","platform":"LeetCode"},
        {"name":"Task Scheduler","url":"https://leetcode.com/problems/task-scheduler/","diff":"Medium","platform":"LeetCode"},
    ],
    "Binary Search":[
        {"name":"Binary Search","url":"https://leetcode.com/problems/binary-search/","diff":"Easy","platform":"LeetCode"},
        {"name":"Find Minimum in Rotated Sorted Array","url":"https://leetcode.com/problems/find-minimum-in-rotated-sorted-array/","diff":"Medium","platform":"LeetCode"},
    ],
    "Trees":[
        {"name":"Maximum Depth of Binary Tree","url":"https://leetcode.com/problems/maximum-depth-of-binary-tree/","diff":"Easy","platform":"LeetCode"},
        {"name":"Validate Binary Search Tree","url":"https://leetcode.com/problems/validate-binary-search-tree/","diff":"Medium","platform":"LeetCode"},
    ],
}

def generate_daily_challenges(user_id, get_db_fn):
    today=datetime.now().strftime("%Y-%m-%d")
    db=get_db_fn()
    rows=db.execute("SELECT * FROM daily_challenges WHERE user_id=? AND assigned_date=?",
                    (user_id,today)).fetchall()
    if rows: db.close(); return [dict(r) for r in rows]
    subs=db.execute("SELECT tags,difficulty,problem_url FROM submissions WHERE user_id=?",
                    (user_id,)).fetchall()
    db.close()
    solved={s["problem_url"] for s in subs}
    tc=Counter()
    for s in subs:
        for t in (s["tags"] or "").split(";"):
            t=t.strip()
            if t: tc[t]+=1
    topics=sorted(PROBLEM_POOL.keys(), key=lambda t:tc.get(t,0))
    import random; random.seed(today)
    chosen=[]
    for topic in topics:
        if len(chosen)>=3: break
        pool=list(PROBLEM_POOL[topic]); random.shuffle(pool)
        for p in pool:
            if p["url"] not in solved:
                chosen.append({**p,"topic":topic}); break
    if len(chosen)<3:
        flat=[p for v in PROBLEM_POOL.values() for p in v]; random.shuffle(flat)
        for p in flat:
            if len(chosen)>=3: break
            if p["url"] not in solved and p not in chosen:
                topic=next((k for k,v in PROBLEM_POOL.items() if p in v),"General")
                chosen.append({**p,"topic":topic})
    db=get_db_fn()
    for p in chosen:
        db.execute("""INSERT INTO daily_challenges
            (user_id,problem_name,problem_url,difficulty,platform,topic,assigned_date,completed)
            VALUES (?,?,?,?,?,?,?,0)
            ON CONFLICT (user_id, problem_url, assigned_date) DO NOTHING""",
            (user_id,p["name"],p["url"],p["diff"],p["platform"],p["topic"],today))
    db.commit()
    result=db.execute("SELECT * FROM daily_challenges WHERE user_id=? AND assigned_date=?",
                      (user_id,today)).fetchall()
    db.close()
    return [dict(r) for r in result]
