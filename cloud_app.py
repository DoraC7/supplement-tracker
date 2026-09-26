"""Streamlit Community Cloud entry point. Never deploy app.py publicly."""
import os
import time

# One immutable process-wide timezone, set before any UI/database date handling.
# Community Cloud runs Linux and supports tzset; failure must not silently use UTC.
os.environ["TZ"] = "Asia/Taipei"
time.tzset()

from supplemind.ui import main

main(cloud=True)