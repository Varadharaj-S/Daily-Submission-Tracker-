# 📊 Google Sheets Setup Guide
## Complete Step-by-Step — DSA Tracker

---

## ❓ Why is this needed?

Your app pushes data to Google Sheets using the **Google Sheets API**.
To use this API, Google requires you to prove who you are using a
**Service Account** — a special robot account with its own credentials.

The `service_account.json` file contains those credentials.

---

## 🪜 Step-by-Step Setup (15 minutes)

---

### STEP 1 — Create a Google Cloud Project

1. Go to **https://console.cloud.google.com/**
2. Click the project dropdown at the top → **"New Project"**
3. Name it: `dsa-tracker`
4. Click **"Create"**
5. Wait 10 seconds, then select the new project from the dropdown

---

### STEP 2 — Enable APIs

1. In the left menu → click **"APIs & Services"** → **"Library"**
2. Search for **"Google Sheets API"** → click it → click **"Enable"**
3. Go back to Library → search **"Google Drive API"** → **"Enable"**

---

### STEP 3 — Create a Service Account

1. Left menu → **"APIs & Services"** → **"Credentials"**
2. Click **"+ Create Credentials"** → choose **"Service Account"**
3. Fill in:
   - **Service account name**: `dsa-tracker-bot`
   - **Service account ID**: auto-fills as `dsa-tracker-bot`
4. Click **"Create and Continue"**
5. For "Grant this service account access to project":
   - Role: **"Editor"** (or just Basic → Editor)
6. Click **"Continue"** → **"Done"**

---

### STEP 4 — Download the JSON Key

1. You are now on the Credentials page
2. Under "Service Accounts", click the email that was just created
   (looks like: `dsa-tracker-bot@your-project.iam.gserviceaccount.com`)
3. Go to the **"Keys"** tab
4. Click **"Add Key"** → **"Create new key"**
5. Select **"JSON"** → click **"Create"**
6. A file downloads automatically — something like `dsa-tracker-bot-abc123.json`

---

### STEP 5 — Place the File in Your Project

1. Rename the downloaded file to exactly: **`service_account.json`**
2. Move it to your project root folder (same folder as `app.py`)

Your folder should look like:
```
dsa_tracker/
├── app.py
├── bot.py
├── service_account.json    ← HERE
├── requirements.txt
└── ...
```

---

### STEP 6 — Get the Service Account Email

1. Open `service_account.json` in any text editor
2. Find the line: `"client_email": "dsa-tracker-bot@..."`
3. Copy that full email address
   Example: `dsa-tracker-bot@dsa-tracker-123.iam.gserviceaccount.com`

**You need this email in the next step.**

---

### STEP 7 — Share Your Google Sheet

**If you want to use your own existing sheet:**

1. Open your Google Sheet
2. Click **"Share"** (top right)
3. Paste the service account email from Step 6
4. Set permission: **"Editor"**
5. Click **"Send"** (uncheck "Notify people" if you want)
6. Copy your Sheet ID from the URL:
   ```
   https://docs.google.com/spreadsheets/d/[THIS_IS_YOUR_SHEET_ID]/edit
   ```
7. Paste this Sheet ID in DSA Tracker → Settings → Google Sheet ID

**If you want the app to auto-create a sheet:**

- Just enter your email during signup
- After admin approves your account, a sheet is automatically created
  and shared with your email
- The Sheet ID is saved to your account automatically

---

### STEP 8 — Test It

1. Run your app: `python app.py`
2. Log in → Settings → make sure Sheet ID is filled
3. Click **"Sync Now"** on Dashboard
4. You should see: `✅ Sheet updated — X rows in 'username'.`
5. Open your Google Sheet — data should appear!

---

## ✅ Column Format (What Your Sheet Will Look Like)

| DATE | PROGRAM TITLE | LINK | DIFFICULTY | PLATFORM | TOPIC | COUNT |
|------|--------------|------|------------|----------|-------|-------|
| 06-01-2026 | C. 1D puyopuyo | [clickable link] | Medium | AtCoder | Greedy | 1 |
| 08-01-2026 | Binary Array Game | [clickable link] | Easy | Codeforces | games | 1 |

- **DATE** — format: DD-MM-YYYY
- **PROGRAM TITLE** — problem name
- **LINK** — clickable HYPERLINK formula, opens problem directly
- **DIFFICULTY** — Easy / Medium / Hard
- **PLATFORM** — Codeforces / LeetCode / AtCoder
- **TOPIC** — first tag from the problem's tags
- **COUNT** — how many times you've solved problems in this topic

---

## 🔴 Common Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `service_account.json not found` | File missing or wrong location | Place it in the same folder as `app.py` |
| `403 Forbidden` | Sheet not shared with service account | Share sheet with the `client_email` from the JSON |
| `invalid_grant` | System clock wrong | Sync your computer's clock |
| `File not found: service_account.json` on Render/Railway | Deploy env issue | Upload file as secret / environment variable |

---

## 🌐 Deploying on Render (Cloud)

On cloud hosting, you can't place files directly.
Instead, set an environment variable:

1. In `service_account.json`, copy the **entire JSON content**
2. On Render → Environment → Add variable:
   ```
   GOOGLE_CREDS_JSON_CONTENT = {... entire json content ...}
   ```
3. Update `bot.py` `_gc()` function to read from env:

```python
import json, tempfile

def _gc():
    import gspread
    from google.oauth2.service_account import Credentials

    # Try file first
    path = os.environ.get("GOOGLE_CREDS_JSON", "service_account.json")
    if os.path.exists(path):
        creds = Credentials.from_service_account_file(path, scopes=[...])
    else:
        # Read from environment variable
        content = os.environ.get("GOOGLE_CREDS_JSON_CONTENT")
        if not content:
            raise FileNotFoundError("No Google credentials found.")
        info = json.loads(content)
        creds = Credentials.from_service_account_info(info, scopes=[...])

    return gspread.authorize(creds)
```

---

## 📞 Quick Summary

```
1. console.cloud.google.com → New Project
2. Enable: Google Sheets API + Google Drive API
3. Create Service Account → Download JSON key
4. Rename to service_account.json → place next to app.py
5. Share your Google Sheet with the client_email from the JSON
6. Copy Sheet ID → paste in DSA Tracker Settings
7. Click Sync → data appears in your sheet!
```
