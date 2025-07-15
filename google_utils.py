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
    This replaces the previous OAuth2 flow.
    """
    try:
        # Get Service Account credentials from Streamlit secrets
        creds_dict = st.secrets["google_credentials"]
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=SCOPES
        )
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

def create_drive_folder(drive_service, folder_name, parent_id=None):
    """Creates a folder in regular Drive with better error handling."""
    try:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]

        folder = drive_service.files().create(
            body=file_metadata, 
            fields='id, webViewLink'
        ).execute()
        
        folder_id = folder.get('id')
        if folder_id:
            st.success(f"✅ Folder '{folder_name}' created successfully")
        return folder_id, folder.get('webViewLink')
        
    except HttpError as error:
        if error.resp.status == 403:
            st.error("❌ Permission denied creating folder. Trying alternative approach...")
            # Try creating without parent first
            try:
                file_metadata_alt = {
                    'name': folder_name,
                    'mimeType': 'application/vnd.google-apps.folder'
                }
                folder_alt = drive_service.files().create(
                    body=file_metadata_alt, 
                    fields='id, webViewLink'
                ).execute()
                st.warning("⚠️ Folder created in root Drive instead of target location")
                return folder_alt.get('id'), folder_alt.get('webViewLink')
            except:
                st.error(f"Failed to create folder even in root: {error}")
                return None, None
        else:
            st.error(f"HTTP Error creating folder '{folder_name}': {error}")
            return None, None
    except Exception as e:
        st.error(f"Unexpected error creating folder '{folder_name}': {e}")
        return None, None

def check_service_account_permissions(drive_service):
    """Check if service account has necessary permissions"""
    try:
        # Test if we can list files
        result = drive_service.files().list(pageSize=1).execute()
        
        # Test if we can access the parent folder
        if PARENT_FOLDER_ID:
            folder_info = drive_service.files().get(fileId=PARENT_FOLDER_ID).execute()
            return True, "All permissions OK"
    except HttpError as e:
        if e.resp.status == 403:
            return False, "Permission denied - share folder with service account"
        elif e.resp.status == 404:
            return False, "Parent folder not found - check PARENT_FOLDER_ID"
        else:
            return False, f"HTTP Error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"

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

    # Find or create the MCM periods config file inside the master folder
    if not st.session_state.get('mcm_periods_drive_file_id'):
        mcm_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE, parent_id=MASTER_APP_FOLDER_ID)
        if not mcm_file_id:
            st.info(f"MCM Periods config file not found. Creating it in the master app folder...")
            save_mcm_periods(drive_service, {}) # Create an empty config file
        else:
            st.session_state.mcm_periods_drive_file_id = mcm_file_id

    st.success("Application setup verified successfully.")
    return True


def upload_to_drive(drive_service, file_content_bytes, filename_on_drive):
    """
    Uploads a file to the pre-configured central DAR_UPLOADS_FOLDER_ID.
    The owner of the folder will become the owner of the file.
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
        return file.get('id'), file.get('webViewLink')
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
    """Creates a spreadsheet in root Drive first, then optionally moves it."""
    try:
        # Step 1: Create spreadsheet in root Drive (no parent specified)
        # This should work since your service account can create spreadsheets
        spreadsheet_body = {'properties': {'title': title}}
        spreadsheet = sheets_service.spreadsheets().create(
            body=spreadsheet_body,
            fields='spreadsheetId,spreadsheetUrl'
        ).execute()
        
        spreadsheet_id = spreadsheet.get('spreadsheetId')
        spreadsheet_url = spreadsheet.get('spreadsheetUrl')
        
        if not spreadsheet_id:
            raise Exception("No spreadsheet ID returned")
        
        st.success(f"✅ Spreadsheet '{title}' created successfully in root Drive")
        
        # Step 2: Try to move to target folder (optional - don't fail if this doesn't work)
        if parent_folder_id and drive_service:
            try:
                # Get current parents (should be root)
                file = drive_service.files().get(fileId=spreadsheet_id, fields='parents').execute()
                previous_parents = ",".join(file.get('parents', []))
                
                # Try to move to target folder
                drive_service.files().update(
                    fileId=spreadsheet_id,
                    addParents=parent_folder_id,
                    removeParents=previous_parents,
                    fields='id, parents'
                ).execute()
                st.success(f"✅ Spreadsheet moved to target folder")
                
            except HttpError as move_error:
                # Moving failed, but spreadsheet was created successfully
                st.warning(f"⚠️ Spreadsheet created in root Drive (couldn't move to folder)")
                st.info("The spreadsheet is accessible and functional. You can manually move it if needed.")
                st.info(f"📄 Spreadsheet link: {spreadsheet_url}")
                # Don't fail - the spreadsheet exists and works
                
            except Exception as move_error:
                st.warning(f"⚠️ Spreadsheet created but move failed: {move_error}")
                st.info(f"📄 Spreadsheet link: {spreadsheet_url}")
        
        return spreadsheet_id, spreadsheet_url
        
    except HttpError as error:
        if error.resp.status == 403:
            st.error("❌ Permission denied creating spreadsheet")
            st.error("The service account doesn't have permission to create Google Sheets")
            # Show the service account email for sharing
            st.info("💡 **Solution:** Share your Google Drive with this service account:")
            st.code("nlp-101@supreme-court-hackathoin.iam.gserviceaccount.com")
        else:
            st.error(f"HTTP Error creating spreadsheet: {error}")
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
        if sheet_name == SMART_AUDIT_MASTER_DB_SHEET_NAME:
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

def load_mcm_periods(drive_service):
    """Loads the MCM periods configuration file from the master app folder."""
    if 'mcm_periods_drive_file_id' not in st.session_state:
         # Attempt to find it if not in session state
        file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE, parent_id=MASTER_APP_FOLDER_ID)
        if not file_id:
            st.warning("MCM periods config file not found. A new one will be created on save.")
            return {}
        st.session_state.mcm_periods_drive_file_id = file_id

    mcm_periods_file_id = st.session_state.mcm_periods_drive_file_id
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
        st.error(f"Error loading MCM config: {error}")
        return {}
    except json.JSONDecodeError:
        st.error("MCM config file is corrupted. Returning empty config.")
        return {}
    return {}


def save_mcm_periods(drive_service, periods_data):
    """Saves the MCM periods configuration file to the master app folder."""
    file_content = json.dumps(periods_data, indent=4).encode('utf-8')
    fh = BytesIO(file_content)
    media_body = MediaIoBaseUpload(fh, mimetype='application/json', resumable=True)

    mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')

    try:
        if mcm_periods_file_id:
            # Update existing file
            drive_service.files().update(
                fileId=mcm_periods_file_id,
                media_body=media_body,
                supportsAllDrives=True
            ).execute()
        else:
            # Create new file
            file_metadata = {'name': MCM_PERIODS_FILENAME_ON_DRIVE, 'parents': [MASTER_APP_FOLDER_ID]}
            new_file = drive_service.files().create(
                body=file_metadata,
                media_body=media_body,
                fields='id',
                supportsAllDrives=True
            ).execute()
            st.session_state.mcm_periods_drive_file_id = new_file.get('id')
        return True
    except HttpError as error:
        st.error(f"Error saving MCM config file: {error}")
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


def create_spreadsheet(sheets_service, drive_service, title, parent_folder_id=None):
    """Creates a spreadsheet using Drive API (which is working) instead of Sheets API."""
    try:
        # Use Drive API to create the spreadsheet file
        file_metadata = {
            'name': title,
            'mimeType': 'application/vnd.google-apps.spreadsheet'
        }
        
        if parent_folder_id:
            file_metadata['parents'] = [parent_folder_id]
        
        # Create using Drive API (this is working for you)
        file = drive_service.files().create(
            body=file_metadata,
            fields='id, webViewLink, name'
        ).execute()
        
        spreadsheet_id = file.get('id')
        
        if spreadsheet_id:
            # Generate the correct spreadsheet URL
            spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
            st.success(f"✅ Spreadsheet '{title}' created successfully using Drive API")
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
        if sheet_name == SMART_AUDIT_MASTER_DB_SHEET_NAME:
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

def read_from_spreadsheet(sheets_service, spreadsheet_id, sheet_name="Sheet1"):
    """Reads an entire sheet into a pandas DataFrame, handling varying column counts."""
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=sheet_name
        ).execute()
        values = result.get('values', [])

        if not values:
            return pd.DataFrame()

        header = values[0]
        data = values[1:]
        
        if not data:
            return pd.DataFrame(columns=header)

        num_cols = len(header)
        processed_data = []
        for row in data:
            new_row = list(row)
            if len(new_row) < num_cols:
                new_row.extend([None] * (num_cols - len(new_row)))
            elif len(new_row) > num_cols:
                new_row = new_row[:num_cols]
            processed_data.append(new_row)

        df = pd.DataFrame(processed_data, columns=header)
        return df

    except HttpError as error:
        st.error(f"An API error occurred reading from Spreadsheet: {error}")
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

def load_mcm_periods(drive_service):
    """Loads the MCM periods configuration file from Google Drive."""
    mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
    if not mcm_periods_file_id:
        if st.session_state.get('master_drive_folder_id'):
            mcm_periods_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE,
                                                          parent_id=st.session_state.master_drive_folder_id)
            st.session_state.mcm_periods_drive_file_id = mcm_periods_file_id
        else:
            return {}

    if mcm_periods_file_id:
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
                st.session_state.mcm_periods_drive_file_id = None
            else:
                st.error(f"Error loading '{MCM_PERIODS_FILENAME_ON_DRIVE}' from Drive: {error}")
            return {}
        except json.JSONDecodeError:
            st.error(f"Error decoding JSON from '{MCM_PERIODS_FILENAME_ON_DRIVE}'. File might be corrupted.")
            return {}
        except Exception as e:
            st.error(f"Unexpected error loading '{MCM_PERIODS_FILENAME_ON_DRIVE}': {e}")
            return {}
    return {}

def save_mcm_periods(drive_service, periods_data):
    """Saves the MCM periods configuration file to Google Drive."""
    master_folder_id = st.session_state.get('master_drive_folder_id')
    if not master_folder_id:
        st.error("Master Drive folder ID not set. Cannot save MCM periods configuration.")
        return False

    mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
    file_content = json.dumps(periods_data, indent=4).encode('utf-8')
    fh = BytesIO(file_content)
    media_body = MediaIoBaseUpload(fh, mimetype='application/json', resumable=True)

    try:
        if mcm_periods_file_id:
            file_metadata_update = {'name': MCM_PERIODS_FILENAME_ON_DRIVE}
            drive_service.files().update(
                fileId=mcm_periods_file_id,
                body=file_metadata_update,
                media_body=media_body,
                fields='id, name'
            ).execute()
        else:
            file_metadata_create = {'name': MCM_PERIODS_FILENAME_ON_DRIVE, 'parents': [master_folder_id]}
            new_file = drive_service.files().create(
                body=file_metadata_create,
                media_body=media_body,
                fields='id, name'
            ).execute()
            st.session_state.mcm_periods_drive_file_id = new_file.get('id')
        return True
    except HttpError as error:
        st.error(f"Error saving '{MCM_PERIODS_FILENAME_ON_DRIVE}' to Drive: {error}")
        return False
    except Exception as e:
        st.error(f"Unexpected error saving '{MCM_PERIODS_FILENAME_ON_DRIVE}': {e}")
        return False

def append_to_spreadsheet(sheets_service, spreadsheet_id, values_to_append):
    """Appends rows to a spreadsheet."""
    try:
        body = {'values': values_to_append}
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = sheet_metadata.get('sheets', '')
        first_sheet_title = sheets[0].get("properties", {}).get("title", "Sheet1")

        append_result = sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{first_sheet_title}!A1",
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        return append_result
    except HttpError as error:
        st.error(f"An error occurred appending to Spreadsheet: {error}")
        return None
    except Exception as e:
        st.error(f"Unexpected error appending to Spreadsheet: {e}")
        return None

def delete_spreadsheet_rows(sheets_service, spreadsheet_id, sheet_id_gid, row_indices_to_delete):
    """Deletes specific rows from a sheet."""
    if not row_indices_to_delete:
        return True
    requests = []
    # Sort in descending order to avoid index shifting issues during deletion
    for data_row_index in sorted(row_indices_to_delete, reverse=True):
        # The API uses 0-based index. If data starts at row 2 (index 1) after the header,
        # the sheet row index for the API is data_row_index + 1.
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
        try:
            body = {'requests': requests}
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body=body).execute()
            return True
        except HttpError as error:
            st.error(f"An error occurred deleting rows from Spreadsheet: {error}")
            return False
        except Exception as e:
            st.error(f"Unexpected error deleting rows: {e}")
            return False
    return True# # google_utils.py
def test_permissions_debug(drive_service, sheets_service):
    """Test function to debug permissions issues"""
    st.subheader("🔍 Permission Diagnostic Test")
    
    if st.button("Run Permission Test"):
        results = []
        
        # Test 1: Basic Drive access
        try:
            drive_service.files().list(pageSize=1).execute()
            results.append("✅ Basic Drive access: OK")
        except Exception as e:
            results.append(f"❌ Basic Drive access: {e}")
        
        # Test 2: Parent folder access
        try:
            folder_info = drive_service.files().get(fileId=PARENT_FOLDER_ID).execute()
            results.append(f"✅ Parent folder access: OK - {folder_info.get('name')}")
        except Exception as e:
            results.append(f"❌ Parent folder access: {e}")
        
        # Test 3: Create spreadsheet in root
        try:
            test_sheet = sheets_service.spreadsheets().create(
                body={'properties': {'title': 'TEST_PERMISSIONS_DELETE_ME'}}
            ).execute()
            sheet_id = test_sheet.get('spreadsheetId')
            results.append("✅ Create spreadsheet in root: OK")
            
            # Clean up test sheet
            try:
                drive_service.files().delete(fileId=sheet_id).execute()
                results.append("✅ Cleanup test sheet: OK")
            except:
                results.append(f"⚠️ Test sheet created but not cleaned up: {sheet_id}")
                
        except Exception as e:
            results.append(f"❌ Create spreadsheet in root: {e}")
        
        # Test 4: Create folder in parent
        try:
            test_folder = drive_service.files().create(
                body={
                    'name': 'TEST_PERMISSIONS_DELETE_ME',
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [PARENT_FOLDER_ID]
                }
            ).execute()
            folder_id = test_folder.get('id')
            results.append("✅ Create folder in parent: OK")
            
            # Clean up test folder
            try:
                drive_service.files().delete(fileId=folder_id).execute()
                results.append("✅ Cleanup test folder: OK")
            except:
                results.append(f"⚠️ Test folder created but not cleaned up: {folder_id}")
                
        except Exception as e:
            results.append(f"❌ Create folder in parent: {e}")
        
        # Display results
        for result in results:
            if "✅" in result:
                st.success(result)
            elif "❌" in result:
                st.error(result)
            else:
                st.warning(result)
        
        # Show service account info if available
        try:
            about = drive_service.about().get(fields='user').execute()
            user_info = about.get('user', {})
            st.info(f"Service account email: {user_info.get('emailAddress', 'Unknown')}")
        except:
            st.warning("Could not retrieve service account email")
def test_root_spreadsheet_creation(sheets_service, drive_service):
    """Test creating spreadsheet in root Drive"""
    st.subheader("🧪 Test Spreadsheet Creation in Root")
    
    if st.button("Test Create in Root Drive"):
        test_title = f"TEST_ROOT_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        with st.spinner("Testing..."):
            try:
                # Create without any parent folder
                spreadsheet_body = {'properties': {'title': test_title}}
                spreadsheet = sheets_service.spreadsheets().create(
                    body=spreadsheet_body,
                    fields='spreadsheetId,spreadsheetUrl'
                ).execute()
                
                spreadsheet_id = spreadsheet.get('spreadsheetId')
                spreadsheet_url = spreadsheet.get('spreadsheetUrl')
                
                if spreadsheet_id:
                    st.success("✅ SUCCESS! Spreadsheet created in root Drive")
                    st.info(f"**ID:** {spreadsheet_id}")
                    st.info(f"**URL:** {spreadsheet_url}")
                    
                    # Clean up test file
                    try:
                        drive_service.files().delete(fileId=spreadsheet_id).execute()
                        st.success("✅ Test file cleaned up")
                    except:
                        st.warning("⚠️ Test file created but not cleaned up")
                        st.info("You can manually delete it from your Drive")
                else:
                    st.error("❌ No spreadsheet ID returned")
                    
            except Exception as e:
                st.error(f"❌ Test failed: {e}")


