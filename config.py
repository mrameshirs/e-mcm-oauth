# config.py
import streamlit as st

# --- Google API Configuration ---
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

# --- Google Drive Master Configuration ---
# Instructions for the App Administrator (PCO):
# 1. In your personal Google Drive, create a main folder for the app, e.g., "e-MCM App Files".
# 2. Inside that folder, create another folder named "All DAR Uploads".
# 3. Create a new Google Sheet named "Master DAR Database".
# 4. Share BOTH the "All DAR Uploads" folder AND the "Master DAR Database" sheet with your service account's email address, granting it "Editor" permissions.
# 5. Paste the ID of the "All DAR Uploads" folder and the "Master DAR Database" sheet below.

# The folder where all monthly sub-folders and config files will be stored.
# Get this from the URL of the folder in your personal Google Drive.
MASTER_APP_FOLDER_ID = "1g1dgq5Ci_tPaqq1q2XuI7hMjiuQxDjFc" 

# The single folder where all DAR PDFs from all groups and months will be uploaded.
# This folder should be INSIDE your MASTER_APP_FOLDER_ID.
DAR_UPLOADS_FOLDER_ID = "1wptb8HtZAeFFBOJPSAJJEDvTTsQiwN2c"

# The single spreadsheet that will store all extracted data from all DARs.
DAR_MASTER_SPREADSHEET_ID = "1zpkKj5hmprxpXxHuj_68hOVdBg24IwF6tFIizGU-wec"

# --- Internal Configuration Files (will be stored in MASTER_APP_FOLDER_ID) ---
MCM_PERIODS_FILENAME_ON_DRIVE = "mcm_periods_config.json"
LOG_SHEET_FILENAME_ON_DRIVE = "e-MCM_App_Activity_Log"
SMART_AUDIT_MASTER_DB_SHEET_NAME = "smart_audit_master_db"


# --- User Credentials (No changes here) ---
USER_CREDENTIALS = {
    "planning_officer": "pco_password",
    **{f"audit_group{i}": f"ag{i}_audit" for i in range(1, 31)}
}
USER_ROLES = {
    "planning_officer": "PCO",
    **{f"audit_group{i}": "AuditGroup" for i in range(1, 31)}
}
AUDIT_GROUP_NUMBERS = {
    f"audit_group{i}": i for i in range(1, 31)
}
