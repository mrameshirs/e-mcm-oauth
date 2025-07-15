# app.py
import streamlit as st
import pandas as pd

# Set page configuration once at the top
st.set_page_config(layout="wide", page_title="e-MCM App - GST Audit 1")

# --- Custom Module Imports ---
from css_styles import load_custom_css
from google_utils import get_google_services, initialize_drive_structure, log_activity
from ui_login import login_page
from ui_pco import pco_dashboard
from ui_audit_group import audit_group_dashboard
from ui_smart_audit_tracker import smart_audit_tracker_dashboard, audit_group_tracker_view

# Load custom CSS styles
load_custom_css()

# --- Session State Initialization ---
def initialize_session_state():
    """Initializes all required session state variables."""
    states = {
        'logged_in': False,
        'username': "",
        'role': "",
        'audit_group_no': None,
        'ag_current_extracted_data': [],
        'ag_pdf_drive_url': None,
        'ag_validation_errors': [],
        'ag_editor_data': pd.DataFrame(),
        'ag_current_mcm_key': None,
        'ag_current_uploaded_file_name': None,
        'master_drive_folder_id': None,
        'mcm_periods_drive_file_id': None,
        'drive_structure_initialized': False,
        'login_event_logged': False,
        'log_sheet_id': None,
        'app_mode': "e-mcm"  # Default mode: 'e-mcm' or 'smart_audit_tracker'
    }
    for key, value in states.items():
        if key not in st.session_state:
            st.session_state[key] = value

initialize_session_state()

# --- Main Application Logic ---
if not st.session_state.logged_in:
    login_page()
else:
    # --- Service and Structure Initialization (Simplified and Combined) ---
    # This block runs once after login to ensure everything is ready.
    if not st.session_state.get('drive_structure_initialized'):
        with st.spinner("Connecting to Google Services and verifying setup..."):
            
            # Step 1: Get Google services using Service Account
            drive_service, sheets_service = get_google_services()
            if not drive_service or not sheets_service:
                st.error("Fatal Error: Could not connect to Google Services. Please check the service account credentials in Streamlit secrets.")
                st.stop()
            
            # Store services in session state for other modules to use
            st.session_state.drive_service = drive_service
            st.session_state.sheets_service = sheets_service

            # Step 2: Verify the Drive/Sheet structure defined in config.py
            if initialize_drive_structure(drive_service, sheets_service):
                st.session_state.drive_structure_initialized = True
                
                # Step 3: Log the login activity after structure is confirmed
                if not st.session_state.get('login_event_logged'):
                    log_sheet_id = st.session_state.get('log_sheet_id')
                    if log_sheet_id:
                        log_activity(sheets_service, log_sheet_id, st.session_state.username, st.session_state.role)
                        st.session_state.login_event_logged = True
                    else:
                        st.warning("Could not find log sheet ID. Login activity will not be recorded.")
                
                # Rerun to display the main dashboard
                st.rerun()
            else:
                st.error("Fatal Error: Failed to initialize application structure in Google Drive. Application cannot proceed. Please check the folder/sheet IDs in config.py and their sharing permissions with the service account.")
                st.stop()

    # --- View Routing Logic (based on App Mode and Role) ---
    # This section is only reached after the above initialization is successful.
    if st.session_state.get('drive_structure_initialized'):
        drive_service = st.session_state.drive_service
        sheets_service = st.session_state.sheets_service
        
        # Route to the correct dashboard based on the selected app mode and user role
        if st.session_state.app_mode == "smart_audit_tracker":
            if st.session_state.role == "PCO":
                smart_audit_tracker_dashboard(drive_service, sheets_service)
            elif st.session_state.role == "AuditGroup":
                audit_group_tracker_view(drive_service, sheets_service)
        else:  # Default to "e-mcm" mode
            if st.session_state.role == "PCO":
                pco_dashboard(drive_service, sheets_service)
            elif st.session_state.role == "AuditGroup":
                audit_group_dashboard(drive_service, sheets_service)
            else:
                st.error("Unknown user role. Please log in again.")
                st.session_state.logged_in = False
                st.rerun()

    # Fallback message if logged in but services failed initialization for any reason
    elif st.session_state.logged_in:
        st.warning("Google services are not available. Please check configuration and network. Try logging out and back in.")
        if st.button("Logout", key="main_logout_gerror"):
            st.session_state.logged_in = False
            st.rerun()
