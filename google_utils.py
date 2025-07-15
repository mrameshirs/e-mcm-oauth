# google_utils.py
from datetime import datetime 
import streamlit as st
import os
import json
from io import BytesIO
import pandas as pd
import math

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload

# Import the new centralized config variables
from config import (
    SCOPES,
    MASTER_APP_FOLDER_ID,
    DAR_UPLOADS_FOLDER_ID,
    DAR_MASTER_SPREADSHEET_ID,
    MCM_PERIODS_FILENAME_ON_DRIVE,
    LOG_SHEET_FILENAME_ON_DRIVE
)

def get_google_services():
    """
    Initializes and returns Google Drive and Sheets services using Service Account credentials.
    Uses impersonation to act on behalf of a human user to avoid storage quota issues.
    """
    try:
        # Get Service Account credentials from Streamlit secrets
        creds_dict = st.secrets["google_credentials"]
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=SCOPES
        )
        
        # Check if we have an impersonation email configured
        impersonation_email = st.secrets.get("impersonation_email")
        if impersonation_email:
            # Use domain-wide delegation to impersonate a human user
            # This uses the human user's storage quota instead of service account's
            creds = creds.with_subject(impersonation_email)
            st.info(f"🔄 Using impersonation for: {impersonation_email}")
        else:
            st.warning("⚠️ No impersonation email configured. Service account storage limits may apply.")
            st.info("💡 Add 'impersonation_email' to Streamlit secrets to use a human user's storage quota.")
        
        drive_service = build('drive', 'v3', credentials=creds)
        sheets_service = build('sheets', 'v4', credentials=creds)
        return drive_service, sheets_service
    except KeyError:
        st.error("Google credentials ('google_credentials') not found in Streamlit secrets.")
        return None, None
    except Exception as e:
        st.error(f"Failed to initialize Google services using Service Account: {e}")
        return None, None

def find_drive_item_by_name(drive_service, name, mime_type=None, parent_id=None):
    """Finds a file or folder by name within a specific parent folder."""
    query = f"name = '{name}' and trashed = false"
    if mime_type:
        query += f" and mimeType = '{mime_type}'"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    else:
        # If no parent is specified, search in the root of the drive the service account has access to.
        # This is generally not recommended for this app's structure but can be useful for debugging.
        st.warning(f"Searching for '{name}' without a parent folder. This may yield unexpected results.")

    try:
        response = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            # These flags are important for ensuring all accessible locations are searched
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()
        items = response.get('files', [])
        return items[0].get('id') if items else None
    except HttpError as error:
        st.error(f"API error searching for '{name}': {error}")
    except Exception as e:
        st.error(f"Unexpected error searching for '{name}': {e}")
    return None

def set_public_read_permission(drive_service, file_id):
    """Sets public read permission for a file - adopted from version 1"""
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except HttpError as error:
        st.warning(f"Could not set public read permission for file ID {file_id}: {error}.")
    except Exception as e:
        st.warning(f"Unexpected error setting public permission for file ID {file_id}: {e}")

def create_drive_folder(drive_service, folder_name, parent_id=None):
    """Creates a folder in shared Drive space (not service account storage) - adopted from version 1"""
    try:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]

        folder = drive_service.files().create(
            body=file_metadata, 
            fields='id, webViewLink',
            supportsAllDrives=True  # Important for shared folders
        ).execute()
        
        folder_id = folder.get('id')
        if folder_id:
            set_public_read_permission(drive_service, folder_id)  # Optional: make folder publicly readable
            st.success(f"✅ Folder '{folder_name}' created successfully")
        return folder_id, folder.get('webViewLink')
        
    except HttpError as error:
        st.error(f"HTTP Error creating folder '{folder_name}': {error}")
        return None, None
    except Exception as e:
        st.error(f"Unexpected error creating folder '{folder_name}': {e}")
        return None, None

def initialize_drive_structure(drive_service, sheets_service):
    """
    Verifies that the configured folders and files exist and are accessible.
    It no longer creates these resources, as they are now manually set up.
    """
    st.info("Verifying application setup...")

    # Check for placeholder values in config
    if "YOUR_" in MASTER_APP_FOLDER_ID or "YOUR_" in DAR_UPLOADS_FOLDER_ID or "YOUR_" in DAR_MASTER_SPREADSHEET_ID:
        st.error("Configuration Incomplete: Please set the folder and sheet IDs in `config.py`.")
        return False

    # Verify Master App Folder
    try:
        drive_service.files().get(fileId=MASTER_APP_FOLDER_ID, fields='id').execute()
        st.session_state.master_drive_folder_id = MASTER_APP_FOLDER_ID
    except HttpError as e:
        st.error(f"Could not access Master App Folder (ID: {MASTER_APP_FOLDER_ID}). Error: {e}")
        st.error("Please ensure the ID is correct and the folder is shared with the service account.")
        return False

    # Verify DAR Uploads Folder
    try:
        drive_service.files().get(fileId=DAR_UPLOADS_FOLDER_ID, fields='id').execute()
    except HttpError as e:
        st.error(f"Could not access DAR Uploads Folder (ID: {DAR_UPLOADS_FOLDER_ID}). Error: {e}")
        return False

    # Verify Master Spreadsheet
    try:
        sheets_service.spreadsheets().get(spreadsheetId=DAR_MASTER_SPREADSHEET_ID).execute()
    except HttpError as e:
        st.error(f"Could not access Master DAR Spreadsheet (ID: {DAR_MASTER_SPREADSHEET_ID}). Error: {e}")
        return False

    # Always look for MCM periods config file in root Drive only (never in parent folders)
    if not st.session_state.get('mcm_periods_drive_file_id'):
        # Search ONLY in root Drive (no parent_id specified) to avoid storage quota issues
        mcm_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE)
        if mcm_file_id:
            st.session_state.mcm_periods_drive_file_id = mcm_file_id
            st.info(f"📄 Found existing MCM config file in root Drive")
        else:
            st.info(f"📄 MCM config file will be created in root Drive when first period is added")

    st.success("Application setup verified successfully.")
    return True

def upload_to_drive(drive_service, file_content_bytes, filename_on_drive):
    """
    Uploads a file to the pre-configured central DAR_UPLOADS_FOLDER_ID.
    Uses shared folder storage, not service account storage.
    """
    try:
        file_metadata = {
            'name': filename_on_drive,
            'parents': [DAR_UPLOADS_FOLDER_ID] # Always upload to the central folder
        }
        fh = BytesIO(file_content_bytes)
        media_body = MediaIoBaseUpload(fh, mimetype='application/pdf', resumable=True)

        file = drive_service.files().create(
            body=file_metadata,
            media_body=media_body,
            fields='id, webViewLink',
            supportsAllDrives=True # Important for shared folders
        ).execute()
        
        file_id = file.get('id')
        if file_id:
            set_public_read_permission(drive_service, file_id)  # Optional: make file publicly readable
        return file_id, file.get('webViewLink')
    except HttpError as error:
        st.error(f"An API error occurred uploading to Drive: {error}")
        return None, None
    except Exception as e:
        st.error(f"An unexpected error in upload_to_drive: {e}")
        return None, None

def append_to_spreadsheet(sheets_service, values_to_append):
    """Appends rows to the pre-configured central DAR_MASTER_SPREADSHEET_ID."""
    try:
        body = {'values': values_to_append}
        # Get the title of the first sheet to append to
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=DAR_MASTER_SPREADSHEET_ID).execute()
        first_sheet_title = sheet_metadata.get('sheets', [{}])[0].get('properties', {}).get('title', 'Sheet1')

        append_result = sheets_service.spreadsheets().values().append(
            spreadsheetId=DAR_MASTER_SPREADSHEET_ID,
            range=f"'{first_sheet_title}'!A1", # Use quotes for sheet names with spaces
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        return append_result
    except HttpError as error:
        st.error(f"An error occurred appending to the Master Spreadsheet: {error}")
        return None
    except Exception as e:
        st.error(f"Unexpected error appending to Spreadsheet: {e}")
        return None

def create_spreadsheet(sheets_service, drive_service, title, parent_folder_id=None):
    """Creates a spreadsheet in shared folder space - adopted from version 1 approach"""
    try:
        # Create file metadata with parent folder to use shared storage
        file_metadata = {
            'name': title,
            'mimeType': 'application/vnd.google-apps.spreadsheet'
        }
        
        if parent_folder_id:
            file_metadata['parents'] = [parent_folder_id]
        
        # Create using Drive API in shared folder (uses folder owner's storage)
        file = drive_service.files().create(
            body=file_metadata,
            fields='id, webViewLink, name',
            supportsAllDrives=True  # Important for shared folders
        ).execute()
        
        spreadsheet_id = file.get('id')
        
        if spreadsheet_id:
            # Set permissions like version 1
            set_public_read_permission(drive_service, spreadsheet_id)
            # Generate the correct spreadsheet URL
            spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
            st.success(f"✅ Spreadsheet '{title}' created successfully")
            return spreadsheet_id, spreadsheet_url
        else:
            raise Exception("No spreadsheet ID returned from Drive API")
            
    except HttpError as error:
        st.error(f"Drive API Error creating spreadsheet: {error}")
        return None, None
    except Exception as e:
        st.error(f"Unexpected error creating spreadsheet: {e}")
        return None, None

def find_or_create_log_sheet(drive_service, sheets_service, parent_folder_id):
    """Finds the log sheet or creates it if it doesn't exist."""
    log_sheet_name = LOG_SHEET_FILENAME_ON_DRIVE
    log_sheet_id = find_drive_item_by_name(drive_service, log_sheet_name,
                                           mime_type='application/vnd.google-apps.spreadsheet',
                                           parent_id=parent_folder_id)
    if log_sheet_id:
        return log_sheet_id
    
    st.info(f"Log sheet '{log_sheet_name}' not found. Creating it...")
    spreadsheet_id, _ = create_spreadsheet(sheets_service, drive_service, log_sheet_name, parent_folder_id=parent_folder_id)
    
    if spreadsheet_id:
        header = [['Timestamp', 'Username', 'Role']]
        body = {'values': header}
        try:
            sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range='Sheet1!A1',
                valueInputOption='USER_ENTERED', body=body
            ).execute()
            st.success(f"Log sheet '{log_sheet_name}' created successfully.")
        except HttpError as error:
            st.error(f"Failed to write header to new log sheet: {error}")
            return None
        return spreadsheet_id
    else:
        st.error(f"Fatal: Failed to create log sheet '{log_sheet_name}'. Logging will be disabled.")
        return None

def log_activity(sheets_service, log_sheet_id, username, role):
    """Appends a login activity record to the specified log sheet."""
    if not log_sheet_id:
        st.warning("Log Sheet ID is not available. Skipping activity logging.")
        return False
    
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        values = [[timestamp, username, role]]
        body = {'values': values}
        
        sheets_service.spreadsheets().values().append(
            spreadsheetId=log_sheet_id,
            range='Sheet1!A1',
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        return True
    except HttpError as error:
        st.error(f"An error occurred while logging activity: {error}")
        return False
    except Exception as e:
        st.error(f"An unexpected error occurred during logging: {e}")
        return False

def find_or_create_spreadsheet(drive_service, sheets_service, sheet_name, parent_folder_id):
    """Finds a spreadsheet by name or creates it with a header if it doesn't exist."""
    sheet_id = find_drive_item_by_name(drive_service, sheet_name,
                                       mime_type='application/vnd.google-apps.spreadsheet',
                                       parent_id=parent_folder_id)
    if sheet_id:
        return sheet_id

    st.info(f"Spreadsheet '{sheet_name}' not found. Creating it...")
    sheet_id, _ = create_spreadsheet(sheets_service, drive_service, sheet_name, parent_folder_id=parent_folder_id)
    
    if sheet_id:
        header = []
        if sheet_name == "SMART_AUDIT_MASTER_DB_SHEET_NAME":  # Replace with actual constant
            header = [[
                "GSTIN", "Trade Name", "Category", "Allocated Audit Group Number", 
                "Allocated Circle", "Financial Year", "Allocated Date", "Uploaded Date", 
                "Office Order PDF Path", "Reassigned Flag", "Old Group Number", "Old Circle Number"
            ]]
        elif sheet_name == LOG_SHEET_FILENAME_ON_DRIVE:
             header = [['Timestamp', 'Username', 'Role']]
        
        if header:
            body = {'values': header}
            try:
                sheets_service.spreadsheets().values().append(
                    spreadsheetId=sheet_id, range='Sheet1!A1',
                    valueInputOption='USER_ENTERED', body=body
                ).execute()
                st.success(f"Spreadsheet '{sheet_name}' created successfully with headers.")
            except HttpError as error:
                st.error(f"Failed to write header to new spreadsheet: {error}")
                return None
        return sheet_id
    else:
        st.error(f"Fatal: Failed to create spreadsheet '{sheet_name}'.")
        return None

def read_from_spreadsheet(sheets_service, sheet_name="Sheet1"):
    """Reads the entire central master spreadsheet into a pandas DataFrame."""
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=DAR_MASTER_SPREADSHEET_ID,
            range=sheet_name
        ).execute()
        values = result.get('values', [])

        if not values:
            return pd.DataFrame()

        header = values[0]
        data = values[1:]

        if not data:
            return pd.DataFrame(columns=header)

        # Pad rows with None if they have fewer columns than the header
        num_cols = len(header)
        processed_data = [row + [None] * (num_cols - len(row)) for row in data]

        df = pd.DataFrame(processed_data, columns=header)
        return df

    except HttpError as error:
        st.error(f"An API error occurred reading from the Master Spreadsheet: {error}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"An unexpected error occurred while reading the Spreadsheet: {e}")
        return pd.DataFrame()

def update_spreadsheet_from_df(sheets_service, spreadsheet_id, df_to_write):
    """Clears a sheet and updates it with data from a pandas DataFrame."""
    try:
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        first_sheet_title = sheet_metadata['sheets'][0]['properties']['title']

        sheets_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=first_sheet_title
        ).execute()

        df_prepared = df_to_write.fillna('')
        values_to_write = [df_prepared.columns.values.tolist()] + df_prepared.values.tolist()

        body = {'values': values_to_write}
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{first_sheet_title}!A1",
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        
        return True

    except HttpError as error:
        st.error(f"An API error occurred while updating the Spreadsheet: {error}")
        return False
    except Exception as e:
        st.error(f"An unexpected error occurred while updating the Spreadsheet: {e}")
        return False

def reset_mcm_file_to_root(drive_service):
    """Helper function to reset MCM file reference and ensure it uses root Drive only."""
    st.session_state.mcm_periods_drive_file_id = None
    # Search for MCM file in root Drive only
    mcm_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE)
    if mcm_file_id:
        st.session_state.mcm_periods_drive_file_id = mcm_file_id
        st.success("✅ MCM file reference reset to use root Drive")
        return True
    else:
        st.info("📄 No MCM file found in root Drive. A new one will be created.")
        return False

def load_mcm_periods(drive_service):
    """Loads the MCM periods configuration file from root Drive (not from parent folder)."""
    mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
    
    if not mcm_periods_file_id:
         # Search for the file in root Drive (no parent_id specified)
        file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE)
        if not file_id:
            st.warning("MCM periods config file not found in root Drive. A new one will be created on save.")
            return {}
        st.session_state.mcm_periods_drive_file_id = file_id
        mcm_periods_file_id = file_id

    # Double-check that we have a valid file ID before proceeding
    if not mcm_periods_file_id:
        st.warning("MCM periods file ID is not available. Returning empty config.")
        return {}

    try:
        request = drive_service.files().get_media(fileId=mcm_periods_file_id)
        fh = BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        return json.load(fh)
    except HttpError as error:
        if error.resp.status == 404:
            # File was deleted or moved, reset the session state
            st.session_state.mcm_periods_drive_file_id = None
            st.warning("MCM periods config file not found. It may have been deleted.")
        else:
            st.error(f"Error loading MCM config: {error}")
        return {}
    except json.JSONDecodeError:
        st.error("MCM config file is corrupted. Returning empty config.")
        return {}
    except Exception as e:
        st.error(f"Unexpected error loading MCM config: {e}")
        return {}

def save_mcm_periods(drive_service, periods_data):
    """Saves the MCM periods configuration file to root Drive (always outside parent folder)."""
    file_content = json.dumps(periods_data, indent=4).encode('utf-8')
    fh = BytesIO(file_content)
    media_body = MediaIoBaseUpload(fh, mimetype='application/json', resumable=True)

    mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')

    try:
        if mcm_periods_file_id:
            # Update existing file (which should be in root Drive)
            drive_service.files().update(
                fileId=mcm_periods_file_id,
                media_body=media_body,
                supportsAllDrives=True
            ).execute()
            st.success(f"✅ MCM config updated successfully")
            return True
        else:
            # Create new file in ROOT Drive (never in parent folder)
            file_metadata = {'name': MCM_PERIODS_FILENAME_ON_DRIVE}
            # Note: No 'parents' key = file always goes to root Drive
            new_file = drive_service.files().create(
                body=file_metadata,
                media_body=media_body,
                fields='id',
                supportsAllDrives=True
            ).execute()
            file_id = new_file.get('id')
            if file_id:
                set_public_read_permission(drive_service, file_id)
                st.session_state.mcm_periods_drive_file_id = file_id
                st.success(f"✅ MCM config file '{MCM_PERIODS_FILENAME_ON_DRIVE}' created in root Drive")
                return True
            else:
                st.error("Failed to create MCM config file")
                return False
                
    except HttpError as error:
        st.error(f"Error saving MCM config file: {error}")
        return False
    except Exception as e:
        st.error(f"Unexpected error saving MCM config file: {e}")
        return False

def delete_spreadsheet_rows(sheets_service, row_indices_to_delete):
    """Deletes specific rows from the central master sheet."""
    if not row_indices_to_delete:
        return True

    try:
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=DAR_MASTER_SPREADSHEET_ID).execute()
        sheet_id_gid = sheet_metadata.get('sheets', [{}])[0].get('properties', {}).get('sheetId', 0)

        requests = []
        # Sort in descending order to avoid index shifting issues
        for data_row_index in sorted(row_indices_to_delete, reverse=True):
            # API uses 0-based index. Header is row 0. Data starts at row 1.
            # The dataframe index corresponds to the data row number.
            sheet_row_start_index = data_row_index + 1
            requests.append({
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id_gid,
                        "dimension": "ROWS",
                        "startIndex": sheet_row_start_index,
                        "endIndex": sheet_row_start_index + 1
                    }
                }
            })

        if requests:
            body = {'requests': requests}
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=DAR_MASTER_SPREADSHEET_ID, body=body).execute()
        return True
    except HttpError as error:
        st.error(f"An error occurred deleting rows: {error}")
        return False
    except Exception as e:
        st.error(f"Unexpected error deleting rows: {e}")
        return False
