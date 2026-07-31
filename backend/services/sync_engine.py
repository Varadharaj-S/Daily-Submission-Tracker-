"""
services/sync_engine.py — re-exports the sync/import/dashboard functions
from bot.py and normal_sync.py, with the same graceful fallbacks app.py had
at the top of the file (so the app still boots in an environment where,
say, selenium/webdriver-manager aren't installed).
"""

try:
    from normal_sync import sync_user_data
except Exception:
    def sync_user_data(*args, **kwargs):
        return None

try:
    from bot import get_dashboard_data, generate_daily_challenges, create_user_sheet, import_lc_full
except Exception:
    def get_dashboard_data(*args, **kwargs):
        return {"chart_labels": [], "chart_data": [], "total": 0, "solved": 0}

    def generate_daily_challenges(*args, **kwargs):
        return []

    def create_user_sheet(*args, **kwargs):
        return None

    def import_lc_full(*args, **kwargs):
        return {"success": False, "message": "LeetCode import unavailable in this environment."}

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:
    webdriver = None
    Service = None
    ChromeDriverManager = None
