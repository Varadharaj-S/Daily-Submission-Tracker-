from selenium import webdriver
from database.db import get_db
import time

def capture_leetcode_cookie_auto(user_id):
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")

    driver = webdriver.Chrome(options=options)

    driver.get("https://leetcode.com/accounts/login/")

    print("👉 Please login in browser...")

    # 🔥 Wait until login success (check URL change)
    for _ in range(60):   # max ~60 sec wait
        if "leetcode.com" in driver.current_url and "login" not in driver.current_url:
            break
        time.sleep(1)

    # Go to homepage to ensure cookies loaded
    driver.get("https://leetcode.com/")
    time.sleep(3)

    cookies = driver.get_cookies()

    session_cookie = ""
    csrf = ""

    for c in cookies:
        if c['name'] == 'LEETCODE_SESSION':
            session_cookie = c['value']
        if c['name'] == 'csrftoken':
            csrf = c['value']

    driver.quit()

    if not session_cookie:
        print("❌ Cookie fetch failed")
        return False

    # Save to DB
    with get_db() as conn:
        conn.execute("""
            UPDATE users
            SET lc_session_cookie=?, lc_csrf_token=?
            WHERE id=?
        """, (session_cookie, csrf, user_id))
        conn.commit()

    print("✅ Cookie saved successfully!")

    return True