"""
contest/contest_api.py — Phase 3, Issues 3 & 4: single interface over each
judge's contest-results API.

*** NOT USED AS OF PHASE 6 ***
contest/contest_sync.py no longer imports or calls anything in this file.
Contest grading now reads straight from the `submissions` table the daily
bot already fills in (see contest_sync.py's module docstring for why: no
AtCoder login, no Cloudflare blocks, no API rate limits, works offline).
This file is left in place only in case a live-standings lookup (rank,
official score) is wanted again later — nothing currently calls it.

Each provider implements one method:

    fetch_results(contest, handles) -> {
        handle: {"solved": int, "rank": int|None, "score": float|None, "participated": bool}
    }

`handles` is only the handles worth looking up (students who filled in a
cf_handle / lc_handle / ac_handle) — not every registered student. A handle
that doesn't show up in the contest's standings comes back with
participated=False, which contest_sync.py + contest_sheet.py turn into an
"ABS" cell on the sheet (they didn't take part).

Adding a new judge later (CodeChef, HackerRank) means writing one new class
here and adding one line to PROVIDERS — nothing in contest_sync.py changes.

IMPORTANT — these three providers could not be tested against the live
judge APIs in the environment they were written in (no outbound network
access there). Each one follows the judge's documented/commonly-used public
endpoint as closely as possible (comments below cite which), but you should
run one real end-to-end sync per platform (create a short real contest,
watch it sync) before trusting this in front of students. If a judge
changes its API shape, the fix is isolated to that one class.
"""

import requests

REQUEST_TIMEOUT = 30

# Codeforces (and some CDNs in front of AtCoder/LeetCode) return an HTML
# error/challenge page instead of JSON for requests that look like a bare
# script — no User-Agent, or a Python-requests default one. Sending a
# normal-browser-looking User-Agent avoids most of that.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def _safe_json(resp, provider_name):
    """resp.json() but with an error message that actually says what went
    wrong — a bare JSONDecodeError ("Expecting value: line 4 column 1")
    gives no clue whether the API blocked the request, rate-limited it, or
    something else. This surfaces the HTTP status and a snippet of the raw
    body instead, so a failure is diagnosable straight from
    contest_sync_log / the scheduler's stdout, no reproduction needed."""
    try:
        return resp.json()
    except ValueError:
        snippet = (resp.text or "")[:200].replace("\n", " ").strip()
        raise RuntimeError(
            f"{provider_name} did not return JSON (HTTP {resp.status_code}): {snippet!r}"
        )


def _empty_result_map(handles):
    return {h: {"solved": 0, "rank": None, "score": None, "participated": False} for h in handles}


class ContestProvider:
    platform = None

    def fetch_results(self, contest, handles):
        """contest: dict from contest_events (contest_code is expected to be
        the platform's own contest identifier — see each subclass for the
        exact format, and contest_create.html's field hint). handles: list
        of this platform's handles to look up. Returns a dict as described
        in the module docstring. Raises on API/network failure — the caller
        (contest_sync.py) is responsible for catching, logging, and
        retrying."""
        raise NotImplementedError


class CodeforcesProvider(ContestProvider):
    """contest_code = the numeric Codeforces contest id, e.g. "2054" for
    https://codeforces.com/contest/2054 — that's also what the contest.standings
    endpoint expects as contestId.

    Docs: https://codeforces.com/apiHelp/methods#contest.standings
    One API call gets every requested handle's row at once (Issue 5's
    "batch, don't loop per-student" principle applies just as much to the
    judge API calls as it does to the Sheet writes)."""

    platform = "Codeforces"

    def fetch_results(self, contest, handles):
        results = _empty_result_map(handles)
        if not handles:
            return results

        contest_id = contest.get("contest_code")
        resp = requests.get(
            "https://codeforces.com/api/contest.standings",
            params={
                "contestId": contest_id,
                "handles": ";".join(handles),
                "showUnofficial": "true",
            },
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        data = _safe_json(resp, "Codeforces")
        if data.get("status") != "OK":
            raise RuntimeError(f"Codeforces API error: {data.get('comment', 'unknown error')}")

        for row in data.get("result", {}).get("rows", []):
            # A problem counts as solved if it has positive points (scored
            # contests) or a recorded best-submission time (ICPC-style
            # pass/fail contests, where points stays 0 even when solved).
            solved = sum(
                1 for pr in row.get("problemResults", [])
                if pr.get("points", 0) > 0 or pr.get("bestSubmissionTimeSeconds") is not None
            )
            for member in row.get("party", {}).get("members", []):
                handle = member.get("handle")
                if handle in results:
                    results[handle] = {
                        "solved": solved,
                        "rank": row.get("rank"),
                        "score": row.get("points"),
                        "participated": True,
                    }
        return results


class LeetCodeProvider(ContestProvider):
    """contest_code = the LeetCode contest slug, e.g. "weekly-contest-452"
    for https://leetcode.com/contest/weekly-contest-452.

    LeetCode has no official public contest-results API; this uses the same
    unauthenticated ranking endpoint the contest results page itself calls
    (leetcode.com/contest/api/ranking/<slug>/?pagination=N), paginating
    until every requested handle is found or the page cap is hit. This is
    the heaviest of the three providers (LeetCode doesn't support "give me
    just these usernames" like Codeforces does), so it's the one most worth
    a real test run before relying on it."""

    platform = "LeetCode"
    MAX_PAGES = 40  # ~40 * 25 rows ≈ 1000 participants scanned before giving up

    def fetch_results(self, contest, handles):
        results = _empty_result_map(handles)
        if not handles:
            return results

        slug = contest.get("contest_code")
        wanted = {h.lower(): h for h in handles}
        found = set()
        page = 1

        while found != set(wanted) and page <= self.MAX_PAGES:
            resp = requests.get(
                f"https://leetcode.com/contest/api/ranking/{slug}/",
                params={"pagination": page, "region": "global"},
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                break
            data = _safe_json(resp, "LeetCode")
            rows = data.get("total_rank", [])
            if not rows:
                break
            submissions = data.get("submissions", [])

            for i, row in enumerate(rows):
                uname = (row.get("username") or "").lower()
                if uname in wanted and uname not in found:
                    sub = submissions[i] if i < len(submissions) else {}
                    solved = len([s for s in sub.values() if s]) if isinstance(sub, dict) else 0
                    results[wanted[uname]] = {
                        "solved": solved,
                        "rank": row.get("rank"),
                        "score": row.get("score"),
                        "participated": True,
                    }
                    found.add(uname)
            page += 1

        return results


class AtCoderProvider(ContestProvider):
    """contest_code = the AtCoder contest id, e.g. "abc300" for
    https://atcoder.jp/contests/abc300.

    Uses the standings JSON endpoint the contest's own standings page loads
    (atcoder.jp/contests/<id>/standings/json) — undocumented but widely used
    for this exact purpose, and it returns every participant in one call."""

    platform = "AtCoder"

    def fetch_results(self, contest, handles):
        results = _empty_result_map(handles)
        if not handles:
            return results

        contest_id = contest.get("contest_code")
        resp = requests.get(
            f"https://atcoder.jp/contests/{contest_id}/standings/json",
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"AtCoder standings fetch failed: HTTP {resp.status_code}")
        data = _safe_json(resp, "AtCoder")

        wanted = {h.lower(): h for h in handles}
        for row in data.get("StandingsData", []):
            uname = (row.get("UserScreenName") or "").lower()
            if uname in wanted:
                total = row.get("TotalResult") or {}
                results[wanted[uname]] = {
                    "solved": total.get("Count", 0),
                    "rank": row.get("Rank"),
                    "score": total.get("Score"),
                    "participated": True,
                }
        return results


PROVIDERS = {
    "Codeforces": CodeforcesProvider(),
    "LeetCode": LeetCodeProvider(),
    "AtCoder": AtCoderProvider(),
}


def get_provider(platform):
    provider = PROVIDERS.get(platform)
    if not provider:
        raise ValueError(f"No contest provider registered for platform '{platform}'")
    return provider
