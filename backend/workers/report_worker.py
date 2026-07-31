"""
workers/report_worker.py — kicks off bot_sheet_sync.py as a subprocess for
a given user. Extracted from the /api/weekly_report route in app.py;
behavior unchanged (fire-and-forget subprocess, same as before) on
Render/local.

Vercel deployment compatibility (PART 3/4): a serverless function's process
is frozen/torn down right after the HTTP response is sent, so a detached
`Popen` child process started here would be killed before
bot_sheet_sync.py finishes. When running on Vercel (VERCEL env var is set
automatically by the platform), this waits for the subprocess to finish
instead, so the report actually gets generated before the response ends.
Same command, same script, same arguments — only fire-and-forget vs.
wait-for-completion changes.

Also uses sys.executable (the interpreter currently running this process)
instead of the bare "python" command: on Vercel's runtime "python" is not
guaranteed to be on PATH, which would fail the subprocess outright.
routes/sync.py's equivalent subprocess call already does this the same
way.
"""

import os
import sys
import subprocess


def run_weekly_report_for_user(user_id):
    if os.environ.get("VERCEL"):
        subprocess.run([sys.executable, "bot_sheet_sync.py", str(user_id)])
    else:
        subprocess.Popen([sys.executable, "bot_sheet_sync.py", str(user_id)])
