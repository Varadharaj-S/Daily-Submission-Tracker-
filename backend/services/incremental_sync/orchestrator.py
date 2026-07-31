"""
services/incremental_sync/orchestrator.py — the incremental replacement
for normal_sync.py's sync_user_data(), built and tested standalone before
being wired into the live sync path (per the "build alongside, don't
touch normal_sync.py yet" plan).

Same return shape as normal_sync.sync_user_data() ({"success", "message",
"new_count"}) so swapping it in later is a one-line change in
services/sync_engine.py, not a rewrite of every caller.
"""

from datetime import datetime

from services.incremental_sync.fetchers import (
    fetch_cf_incremental, fetch_lc_incremental, fetch_ac_incremental
)
from services.incremental_sync.db_ops import get_last_sync_times, set_last_sync, insert_new_submissions
from services.incremental_sync.sheet_ops import get_or_create_user_sheet, append_merged


def sync_user_incremental(user, get_db=None):
    """
    user: dict-like with id, username, cf_handle, lc_handle, ac_handle.
    get_db param kept for call-signature compatibility with
    normal_sync.sync_user_data (routes pass it in) — this module gets its
    own DB connections via database.db.get_db internally either way.
    """
    user_id = user["id"] if hasattr(user, "__getitem__") else user.id
    username = (user.get("username") if hasattr(user, "get") else user["username"]) or f"user_{user_id}"
    cf_handle = (user.get("cf_handle") if hasattr(user, "get") else user["cf_handle"]) or ""
    lc_handle = (user.get("lc_handle") if hasattr(user, "get") else user["lc_handle"]) or ""
    ac_handle = (user.get("ac_handle") if hasattr(user, "get") else user["ac_handle"]) or ""

    last_sync = get_last_sync_times(user_id)

    all_new_items = []
    platform_results = {}

    for platform, handle, fetch_fn, since_key in [
        ("Codeforces", cf_handle, fetch_cf_incremental, "Codeforces"),
        ("LeetCode", lc_handle, fetch_lc_incremental, "LeetCode"),
        ("AtCoder", ac_handle, fetch_ac_incremental, "AtCoder"),
    ]:
        if not handle:
            platform_results[platform] = "skipped (no handle)"
            continue
        try:
            fetched = fetch_fn(handle, last_sync[since_key])
        except Exception as e:
            platform_results[platform] = f"fetch error: {e}"
            continue

        new_items = insert_new_submissions(user_id, fetched)
        all_new_items.extend(new_items)
        set_last_sync(user_id, platform, datetime.now())
        platform_results[platform] = f"{len(new_items)} new (of {len(fetched)} fetched)"

    if not all_new_items:
        return {
            "success": True,
            "message": "No new submissions." + " | " + "; ".join(f"{k}: {v}" for k, v in platform_results.items()),
            "new_count": 0,
        }

    try:
        sheet = get_or_create_user_sheet(username)
        sheet_stats = append_merged(sheet, all_new_items)
        sheet_msg = (f"sheet: +{sheet_stats['appended']} rows, "
                     f"{sheet_stats['blocks_extended']} block(s) extended, "
                     f"{sheet_stats['blocks_created']} new block(s)")
    except Exception as e:
        sheet_msg = f"sheet sync failed: {e}"

    return {
        "success": True,
        "message": f"{len(all_new_items)} new problems added ({sheet_msg}). "
                    + "; ".join(f"{k}: {v}" for k, v in platform_results.items()),
        "new_count": len(all_new_items),
    }
