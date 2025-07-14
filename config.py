# config.py - Updated for OAuth2
import streamlit as st

# # --- Google API Configuration ---
# SCOPES = [
#     'https://www.googleapis.com/auth/drive',
#     'https://www.googleapis.com/auth/spreadsheets',
#     'https://www.googleapis.com/auth/userinfo.profile',
#     'https://www.googleapis.com/auth/userinfo.email'
# ]
# Update your scopes to match what the error shows
SCOPES = [
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email"
]
# --- Google Drive Master Configuration ---
MASTER_DRIVE_FOLDER_NAME = "e-MCM_Root_DAR_App"  # Master folder in user's Google Drive
MCM_PERIODS_FILENAME_ON_DRIVE = "mcm_periods_config.json"  # Config file in Google Drive
LOG_SHEET_FILENAME_ON_DRIVE = "e-MCM_App_Activity_Log" 
SMART_AUDIT_MASTER_DB_SHEET_NAME = "smart_audit_master_db" # Master DB sheet

# Remove shared drive and parent folder references since we're using personal account
PARENT_FOLDER_ID = None  # Not needed for personal account
SHARED_DRIVE_ID = None   # Not using shared drive

# --- User Credentials (keep existing) ---
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
