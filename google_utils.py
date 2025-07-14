# google_utils.py
from datetime import datetime 
import streamlit as st
import os
import json
from io import BytesIO
import pandas as pd
import math

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload

from config import SCOPES, MASTER_DRIVE_FOLDER_NAME, MCM_PERIODS_FILENAME_ON_DRIVE, LOG_SHEET_FILENAME_ON_DRIVE, SMART_AUDIT_MASTER_DB_SHEET_NAME #, PARENT_FOLDER_ID

def get_google_services():
    """Initializes and returns the Google Drive and Sheets service objects using OAuth2."""
    
    # Check if user is already authenticated
    if 'google_credentials' not in st.session_state:
        # Start OAuth flow
        return authenticate_user()
    
    # Use existing credentials
    creds = st.session_state.google_credentials
    
    # Refresh token if expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            st.session_state.google_credentials = creds
        except Exception as e:
            st.error(f"Failed to refresh credentials: {e}")
            st.session_state.pop('google_credentials', None)
            return None, None
    
    try:
        drive_service = build('drive', 'v3', credentials=creds)
        sheets_service = build('sheets', 'v4', credentials=creds)
        return drive_service, sheets_service
    except Exception as e:
        st.error(f"Error building Google services: {e}")
        return None, None

def authenticate_user():
    """Handle OAuth2 authentication flow."""
    
    # Get OAuth2 credentials from Streamlit secrets
    try:
        oauth_config = {
            "web": {
                "client_id": st.secrets["google_oauth"]["client_id"],
                "client_secret": st.secrets["google_oauth"]["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [st.secrets["google_oauth"]["redirect_uri"]]
            }
        }
    except KeyError as e:
        st.error(f"Missing OAuth2 configuration in secrets: {e}")
        st.info("Please add google_oauth section to your secrets.toml")
        return None, None
    
    # Check if we have an authorization code in the URL - FIXED LINE
    if 'code' in st.query_params:
        # Exchange authorization code for credentials
        try:
            flow = Flow.from_client_config(
                oauth_config,
                scopes=SCOPES,
                redirect_uri=oauth_config["web"]["redirect_uris"][0]
            )
            
            # FIXED: Use new query params method
            code = st.query_params['code']
            flow.fetch_token(code=code)
            
            # Store credentials in session state
            st.session_state.google_credentials = flow.credentials
            
            # Clear the code from URL - FIXED
            st.query_params.clear()
            st.rerun()
            
        except Exception as e:
            st.error(f"Failed to exchange authorization code: {e}")
            return None, None
    
    else:
        # Generate authorization URL
        flow = Flow.from_client_config(
            oauth_config,
            scopes=SCOPES,
            redirect_uri=oauth_config["web"]["redirect_uris"][0]
        )
        
        auth_url, _ = flow.authorization_url(prompt='consent')
        
        st.warning("🔐 **Google Account Authentication Required**")
        st.info("To use this application with your personal Google Drive, you need to authorize access.")
        st.markdown(f"👉 **[Click here to authorize with Google]({auth_url})**")
        st.info("After authorization, you will be redirected back to this page automatically.")
        
        return None, None

def get_user_info(drive_service):
    """Get information about the authenticated user."""
    try:
        about = drive_service.about().get(fields='user').execute()
        user = about.get('user', {})
        return {
            'name': user.get('displayName', 'Unknown'),
            'email': user.get('emailAddress', 'Unknown'),
            'photo': user.get('photoLink')
        }
    except Exception:
        return None

def logout_google_account():
    """Clear Google OAuth session data"""
    import streamlit as st
    
    # Clear session state variables related to Google auth
    keys_to_clear = [
        'google_credentials',
        'google_auth_token',
        'user_info',
        'authenticated',
        'access_token',
        'refresh_token'
    ]
    
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]
    
    # Clear query parameters
    st.query_params.clear()
    
    # Force a rerun to update the UI
    st.rerun()

def find_drive_item_by_name(drive_service, name, mime_type=None, parent_id=None):
    """Finds a file or folder by name in user's Drive."""
    query = f"name = '{name}' and trashed = false"
    if mime_type:
        query += f" and mimeType = '{mime_type}'"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    
    try:
        response = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        items = response.get('files', [])
        if items:
            return items[0].get('id')
    except HttpError as error:
        st.warning(f"Error searching for '{name}' in Drive: {error}")
    except Exception as e:
        st.warning(f"Unexpected error searching for '{name}' in Drive: {e}")
    return None

def create_drive_folder(drive_service, folder_name, parent_id=None):
    """Creates a folder in user's Drive."""
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
        st.error(f"❌ Error creating folder '{folder_name}': {error}")
        return None, None
    except Exception as e:
        st.error(f"❌ Unexpected error creating folder '{folder_name}': {e}")
        return None, None

def initialize_drive_structure(drive_service, sheets_service):
    """Initialize folder structure in user's personal Drive."""
    
    # Create main app folder in user's Drive root
    master_id = st.session_state.get('master_drive_folder_id')
    if not master_id:
        master_id = find_drive_item_by_name(drive_service, MASTER_DRIVE_FOLDER_NAME,
                                            'application/vnd.google-apps.folder')
        if not master_id:
            st.info(f"Creating main app folder '{MASTER_DRIVE_FOLDER_NAME}' in your Google Drive...")
            master_id, _ = create_drive_folder(drive_service, MASTER_DRIVE_FOLDER_NAME)
            if not master_id:
                st.error(f"Failed to create main folder '{MASTER_DRIVE_FOLDER_NAME}'.")
                return False
        st.session_state.master_drive_folder_id = master_id

    # Create/find log sheet
    if not st.session_state.get('log_sheet_id'):
        log_sheet_id = find_or_create_log_sheet(drive_service, sheets_service, 
                                               st.session_state.master_drive_folder_id)
        st.session_state.log_sheet_id = log_sheet_id

    # Create/find MCM periods config
    if not st.session_state.get('mcm_periods_drive_file_id'):
        mcm_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE, 
                                              parent_id=st.session_state.master_drive_folder_id)
        if not mcm_file_id:
            st.info(f"Creating MCM periods config file...")
            save_mcm_periods(drive_service, {})
        else:
            st.session_state.mcm_periods_drive_file_id = mcm_file_id

    return True

def upload_to_drive(drive_service, file_content_or_path, folder_id, filename_on_drive):
    """Uploads a file to user's Drive folder."""
    try:
        file_metadata = {'name': filename_on_drive, 'parents': [folder_id]}
        media_body = None

        if isinstance(file_content_or_path, bytes):
            fh = BytesIO(file_content_or_path)
            media_body = MediaIoBaseUpload(fh, mimetype='application/pdf', resumable=True)
        else:
            st.error(f"Unsupported file content type for Google Drive upload: {type(file_content_or_path)}")
            return None, None

        request = drive_service.files().create(
            body=file_metadata,
            media_body=media_body,
            fields='id, webViewLink'
        )
        file = request.execute()
        return file.get('id'), file.get('webViewLink')
    except HttpError as error:
        st.error(f"An API error occurred uploading to Drive: {error}")
        return None, None
    except Exception as e:
        st.error(f"An unexpected error in upload_to_drive: {e}")
        return None, None
        
def check_and_migrate_may_data(drive_service, sheets_service):
    """
    Check if the May 2024 spreadsheet exists and create MCM periods config to include it.
    """
    may_spreadsheet_id = "1-usWIYB-AfAelCjmHdY7H2dVXAydXHmW3iYQA-zFPW4"
    
    try:
        # Test if we can access the May spreadsheet
        result = sheets_service.spreadsheets().get(spreadsheetId=may_spreadsheet_id).execute()
        if result:
            st.success("Found existing May 2024 data! Adding it to MCM periods configuration.")
            # Create MCM periods config with May data
            mcm_periods = {
                "2024-05": {
                    "month_name": "May",
                    "year": 2024,
                    "spreadsheet_id": may_spreadsheet_id,
                    "folder_id": None,  # Will be set later if needed
                    "created_date": "2024-05-01"  # Placeholder date
                }
            }
            return mcm_periods
    except Exception as e:
        st.info(f"May 2024 spreadsheet not accessible or doesn't exist: {e}")
    
    return {}  # Return empty config if May data not found

def create_spreadsheet(sheets_service, drive_service, title, parent_folder_id=None):
    """Creates a spreadsheet in user's Drive."""
    try:
        file_metadata = {
            'name': title,
            'mimeType': 'application/vnd.google-apps.spreadsheet'
        }
        
        if parent_folder_id:
            file_metadata['parents'] = [parent_folder_id]
        
        file = drive_service.files().create(
            body=file_metadata,
            fields='id, webViewLink, name'
        ).execute()
        
        spreadsheet_id = file.get('id')
        
        if spreadsheet_id:
            spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
            st.success(f"✅ Spreadsheet '{title}' created successfully")
            return spreadsheet_id, spreadsheet_url
        else:
            raise Exception("No spreadsheet ID returned")
            
    except HttpError as error:
        st.error(f"Error creating spreadsheet: {error}")
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
        st.error(f"Failed to create log sheet '{log_sheet_name}'.")
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
    return True
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
                st.error(f"❌ Test failed: {e}")# # google_utils.py
# from datetime import datetime 
# import streamlit as st
# import os
# import json
# from io import BytesIO
# import pandas as pd
# import math

# from google.oauth2.credentials import Credentials
# from google_auth_oauthlib.flow import Flow
# from google.auth.transport.requests import Request
# from google.oauth2 import service_account
# from googleapiclient.discovery import build
# from googleapiclient.errors import HttpError
# from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload

# from config import SCOPES, MASTER_DRIVE_FOLDER_NAME, MCM_PERIODS_FILENAME_ON_DRIVE, LOG_SHEET_FILENAME_ON_DRIVE, SMART_AUDIT_MASTER_DB_SHEET_NAME #, PARENT_FOLDER_ID

# def get_google_services():
#     """Initializes and returns the Google Drive and Sheets service objects using OAuth2."""
    
#     # Check if user is already authenticated
#     if 'google_credentials' not in st.session_state:
#         # Start OAuth flow
#         return authenticate_user()
    
#     # Use existing credentials
#     creds = st.session_state.google_credentials
    
#     # Refresh token if expired
#     if creds.expired and creds.refresh_token:
#         try:
#             creds.refresh(Request())
#             st.session_state.google_credentials = creds
#         except Exception as e:
#             st.error(f"Failed to refresh credentials: {e}")
#             st.session_state.pop('google_credentials', None)
#             return None, None
    
#     try:
#         drive_service = build('drive', 'v3', credentials=creds)
#         sheets_service = build('sheets', 'v4', credentials=creds)
#         return drive_service, sheets_service
#     except Exception as e:
#         st.error(f"Error building Google services: {e}")
#         return None, None

# def authenticate_user():
#     """Handle OAuth2 authentication flow."""
    
#     # Get OAuth2 credentials from Streamlit secrets
#     try:
#         oauth_config = {
#             "web": {
#                 "client_id": st.secrets["google_oauth"]["client_id"],
#                 "client_secret": st.secrets["google_oauth"]["client_secret"],
#                 "auth_uri": "https://accounts.google.com/o/oauth2/auth",
#                 "token_uri": "https://oauth2.googleapis.com/token",
#                 "redirect_uris": [st.secrets["google_oauth"]["redirect_uri"]]
#             }
#         }
#     except KeyError as e:
#         st.error(f"Missing OAuth2 configuration in secrets: {e}")
#         st.info("Please add google_oauth section to your secrets.toml")
#         return None, None
    
#     # Check if we have an authorization code in the URL
#     query_params = st.experimental_get_query_params()
    
#     if 'code' in query_params:
#         # Exchange authorization code for credentials
#         try:
#             flow = Flow.from_client_config(
#                 oauth_config,
#                 scopes=SCOPES,
#                 redirect_uri=oauth_config["web"]["redirect_uris"][0]
#             )
            
#             flow.fetch_token(code=query_params['code'][0])
            
#             # Store credentials in session state
#             st.session_state.google_credentials = flow.credentials
            
#             # Clear the code from URL
#             st.experimental_set_query_params()
#             st.rerun()
            
#         except Exception as e:
#             st.error(f"Failed to exchange authorization code: {e}")
#             return None, None
    
#     else:
#         # Generate authorization URL
#         flow = Flow.from_client_config(
#             oauth_config,
#             scopes=SCOPES,
#             redirect_uri=oauth_config["web"]["redirect_uris"][0]
#         )
        
#         auth_url, _ = flow.authorization_url(prompt='consent')
        
#         st.warning("🔐 **Google Account Authentication Required**")
#         st.info("To use this application with your personal Google Drive, you need to authorize access.")
#         st.markdown(f"👉 **[Click here to authorize with Google]({auth_url})**")
#         st.info("After authorization, you will be redirected back to this page automatically.")
        
#         return None, None
# def get_user_info(drive_service):
#     """Get information about the authenticated user."""
#     try:
#         about = drive_service.about().get(fields='user').execute()
#         user = about.get('user', {})
#         return {
#             'name': user.get('displayName', 'Unknown'),
#             'email': user.get('emailAddress', 'Unknown'),
#             'photo': user.get('photoLink')
#         }
#     except Exception:
#         return None

# def logout_google_account():
#     """Clear Google OAuth session data"""
#     import streamlit as st
    
#     # Clear session state variables related to Google auth
#     keys_to_clear = [
#         'google_auth_token',
#         'user_info',
#         'authenticated',
#         'access_token',
#         'refresh_token'
#     ]
    
#     for key in keys_to_clear:
#         if key in st.session_state:
#             del st.session_state[key]
    
#     # Force a rerun to update the UI
#     st.rerun()
# def find_drive_item_by_name(drive_service, name, mime_type=None, parent_id=None):
#     """Finds a file or folder by name in user's Drive."""
#     query = f"name = '{name}' and trashed = false"
#     if mime_type:
#         query += f" and mimeType = '{mime_type}'"
#     if parent_id:
#         query += f" and '{parent_id}' in parents"
    
#     try:
#         response = drive_service.files().list(
#             q=query,
#             spaces='drive',
#             fields='files(id, name)'
#         ).execute()
        
#         items = response.get('files', [])
#         if items:
#             return items[0].get('id')
#     except HttpError as error:
#         st.warning(f"Error searching for '{name}' in Drive: {error}")
#     except Exception as e:
#         st.warning(f"Unexpected error searching for '{name}' in Drive: {e}")
#     return None

# def create_drive_folder(drive_service, folder_name, parent_id=None):
#     """Creates a folder in user's Drive."""
#     try:
#         file_metadata = {
#             'name': folder_name,
#             'mimeType': 'application/vnd.google-apps.folder'
#         }
#         if parent_id:
#             file_metadata['parents'] = [parent_id]

#         folder = drive_service.files().create(
#             body=file_metadata, 
#             fields='id, webViewLink'
#         ).execute()
        
#         folder_id = folder.get('id')
#         if folder_id:
#             st.success(f"✅ Folder '{folder_name}' created successfully")
#         return folder_id, folder.get('webViewLink')
        
#     except HttpError as error:
#         st.error(f"❌ Error creating folder '{folder_name}': {error}")
#         return None, None
#     except Exception as e:
#         st.error(f"❌ Unexpected error creating folder '{folder_name}': {e}")
#         return None, None

# def initialize_drive_structure(drive_service, sheets_service):
#     """Initialize folder structure in user's personal Drive."""
    
#     # Create main app folder in user's Drive root
#     master_id = st.session_state.get('master_drive_folder_id')
#     if not master_id:
#         master_id = find_drive_item_by_name(drive_service, MASTER_DRIVE_FOLDER_NAME,
#                                             'application/vnd.google-apps.folder')
#         if not master_id:
#             st.info(f"Creating main app folder '{MASTER_DRIVE_FOLDER_NAME}' in your Google Drive...")
#             master_id, _ = create_drive_folder(drive_service, MASTER_DRIVE_FOLDER_NAME)
#             if not master_id:
#                 st.error(f"Failed to create main folder '{MASTER_DRIVE_FOLDER_NAME}'.")
#                 return False
#         st.session_state.master_drive_folder_id = master_id

#     # Create/find log sheet
#     if not st.session_state.get('log_sheet_id'):
#         log_sheet_id = find_or_create_log_sheet(drive_service, sheets_service, 
#                                                st.session_state.master_drive_folder_id)
#         st.session_state.log_sheet_id = log_sheet_id

#     # Create/find MCM periods config
#     if not st.session_state.get('mcm_periods_drive_file_id'):
#         mcm_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE, 
#                                               parent_id=st.session_state.master_drive_folder_id)
#         if not mcm_file_id:
#             st.info(f"Creating MCM periods config file...")
#             save_mcm_periods(drive_service, {})
#         else:
#             st.session_state.mcm_periods_drive_file_id = mcm_file_id

#     return True

# def upload_to_drive(drive_service, file_content_or_path, folder_id, filename_on_drive):
#     """Uploads a file to user's Drive folder."""
#     try:
#         file_metadata = {'name': filename_on_drive, 'parents': [folder_id]}
#         media_body = None

#         if isinstance(file_content_or_path, bytes):
#             fh = BytesIO(file_content_or_path)
#             media_body = MediaIoBaseUpload(fh, mimetype='application/pdf', resumable=True)
#         else:
#             st.error(f"Unsupported file content type for Google Drive upload: {type(file_content_or_path)}")
#             return None, None

#         request = drive_service.files().create(
#             body=file_metadata,
#             media_body=media_body,
#             fields='id, webViewLink'
#         )
#         file = request.execute()
#         return file.get('id'), file.get('webViewLink')
#     except HttpError as error:
#         st.error(f"An API error occurred uploading to Drive: {error}")
#         return None, None
#     except Exception as e:
#         st.error(f"An unexpected error in upload_to_drive: {e}")
#         return None, None
        
# def check_and_migrate_may_data(drive_service, sheets_service):
#     """
#     Check if the May 2024 spreadsheet exists and create MCM periods config to include it.
#     """
#     may_spreadsheet_id = "1-usWIYB-AfAelCjmHdY7H2dVXAydXHmW3iYQA-zFPW4"
    
#     try:
#         # Test if we can access the May spreadsheet
#         result = sheets_service.spreadsheets().get(spreadsheetId=may_spreadsheet_id).execute()
#         if result:
#             st.success("Found existing May 2024 data! Adding it to MCM periods configuration.")
#             # Create MCM periods config with May data
#             mcm_periods = {
#                 "2024-05": {
#                     "month_name": "May",
#                     "year": 2024,
#                     "spreadsheet_id": may_spreadsheet_id,
#                     "folder_id": None,  # Will be set later if needed
#                     "created_date": "2024-05-01"  # Placeholder date
#                 }
#             }
#             return mcm_periods
#     except Exception as e:
#         st.info(f"May 2024 spreadsheet not accessible or doesn't exist: {e}")
    
#     return {}  # Return empty config if May data not found

# def create_spreadsheet(sheets_service, drive_service, title, parent_folder_id=None):
#     """Creates a spreadsheet in user's Drive."""
#     try:
#         file_metadata = {
#             'name': title,
#             'mimeType': 'application/vnd.google-apps.spreadsheet'
#         }
        
#         if parent_folder_id:
#             file_metadata['parents'] = [parent_folder_id]
        
#         file = drive_service.files().create(
#             body=file_metadata,
#             fields='id, webViewLink, name'
#         ).execute()
        
#         spreadsheet_id = file.get('id')
        
#         if spreadsheet_id:
#             spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
#             st.success(f"✅ Spreadsheet '{title}' created successfully")
#             return spreadsheet_id, spreadsheet_url
#         else:
#             raise Exception("No spreadsheet ID returned")
            
#     except HttpError as error:
#         st.error(f"Error creating spreadsheet: {error}")
#         return None, None
#     except Exception as e:
#         st.error(f"Unexpected error creating spreadsheet: {e}")
#         return None, None


# def find_or_create_log_sheet(drive_service, sheets_service, parent_folder_id):
#     """Finds the log sheet or creates it if it doesn't exist."""
#     log_sheet_name = LOG_SHEET_FILENAME_ON_DRIVE
#     log_sheet_id = find_drive_item_by_name(drive_service, log_sheet_name,
#                                            mime_type='application/vnd.google-apps.spreadsheet',
#                                            parent_id=parent_folder_id)
#     if log_sheet_id:
#         return log_sheet_id
    
#     st.info(f"Log sheet '{log_sheet_name}' not found. Creating it...")
#     spreadsheet_id, _ = create_spreadsheet(sheets_service, drive_service, log_sheet_name, parent_folder_id=parent_folder_id)
    
#     if spreadsheet_id:
#         header = [['Timestamp', 'Username', 'Role']]
#         body = {'values': header}
#         try:
#             sheets_service.spreadsheets().values().append(
#                 spreadsheetId=spreadsheet_id, range='Sheet1!A1',
#                 valueInputOption='USER_ENTERED', body=body
#             ).execute()
#             st.success(f"Log sheet '{log_sheet_name}' created successfully.")
#         except HttpError as error:
#             st.error(f"Failed to write header to new log sheet: {error}")
#             return None
#         return spreadsheet_id
#     else:
#         st.error(f"Failed to create log sheet '{log_sheet_name}'.")
#         return None

# def log_activity(sheets_service, log_sheet_id, username, role):
#     """Appends a login activity record to the specified log sheet."""
#     if not log_sheet_id:
#         st.warning("Log Sheet ID is not available. Skipping activity logging.")
#         return False
    
#     try:
#         timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#         values = [[timestamp, username, role]]
#         body = {'values': values}
        
#         sheets_service.spreadsheets().values().append(
#             spreadsheetId=log_sheet_id,
#             range='Sheet1!A1',
#             valueInputOption='USER_ENTERED',
#             insertDataOption='INSERT_ROWS',
#             body=body
#         ).execute()
#         return True
#     except HttpError as error:
#         st.error(f"An error occurred while logging activity: {error}")
#         return False
#     except Exception as e:
#         st.error(f"An unexpected error occurred during logging: {e}")
#         return False

# def find_or_create_spreadsheet(drive_service, sheets_service, sheet_name, parent_folder_id):
#     """Finds a spreadsheet by name or creates it with a header if it doesn't exist."""
#     sheet_id = find_drive_item_by_name(drive_service, sheet_name,
#                                        mime_type='application/vnd.google-apps.spreadsheet',
#                                        parent_id=parent_folder_id)
#     if sheet_id:
#         return sheet_id

#     st.info(f"Spreadsheet '{sheet_name}' not found. Creating it...")
#     sheet_id, _ = create_spreadsheet(sheets_service, drive_service, sheet_name, parent_folder_id=parent_folder_id)
    
#     if sheet_id:
#         header = []
#         if sheet_name == SMART_AUDIT_MASTER_DB_SHEET_NAME:
#             header = [[
#                 "GSTIN", "Trade Name", "Category", "Allocated Audit Group Number", 
#                 "Allocated Circle", "Financial Year", "Allocated Date", "Uploaded Date", 
#                 "Office Order PDF Path", "Reassigned Flag", "Old Group Number", "Old Circle Number"
#             ]]
#         elif sheet_name == LOG_SHEET_FILENAME_ON_DRIVE:
#              header = [['Timestamp', 'Username', 'Role']]
        
#         if header:
#             body = {'values': header}
#             try:
#                 sheets_service.spreadsheets().values().append(
#                     spreadsheetId=sheet_id, range='Sheet1!A1',
#                     valueInputOption='USER_ENTERED', body=body
#                 ).execute()
#                 st.success(f"Spreadsheet '{sheet_name}' created successfully with headers.")
#             except HttpError as error:
#                 st.error(f"Failed to write header to new spreadsheet: {error}")
#                 return None
#         return sheet_id
#     else:
#         st.error(f"Fatal: Failed to create spreadsheet '{sheet_name}'.")
#         return None

# def read_from_spreadsheet(sheets_service, spreadsheet_id, sheet_name="Sheet1"):
#     """Reads an entire sheet into a pandas DataFrame, handling varying column counts."""
#     try:
#         result = sheets_service.spreadsheets().values().get(
#             spreadsheetId=spreadsheet_id,
#             range=sheet_name
#         ).execute()
#         values = result.get('values', [])

#         if not values:
#             return pd.DataFrame()

#         header = values[0]
#         data = values[1:]
        
#         if not data:
#             return pd.DataFrame(columns=header)

#         num_cols = len(header)
#         processed_data = []
#         for row in data:
#             new_row = list(row)
#             if len(new_row) < num_cols:
#                 new_row.extend([None] * (num_cols - len(new_row)))
#             elif len(new_row) > num_cols:
#                 new_row = new_row[:num_cols]
#             processed_data.append(new_row)

#         df = pd.DataFrame(processed_data, columns=header)
#         return df

#     except HttpError as error:
#         st.error(f"An API error occurred reading from Spreadsheet: {error}")
#         return pd.DataFrame()
#     except Exception as e:
#         st.error(f"An unexpected error occurred while reading the Spreadsheet: {e}")
#         return pd.DataFrame()

# def update_spreadsheet_from_df(sheets_service, spreadsheet_id, df_to_write):
#     """Clears a sheet and updates it with data from a pandas DataFrame."""
#     try:
#         sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
#         first_sheet_title = sheet_metadata['sheets'][0]['properties']['title']

#         sheets_service.spreadsheets().values().clear(
#             spreadsheetId=spreadsheet_id,
#             range=first_sheet_title
#         ).execute()

#         df_prepared = df_to_write.fillna('')
#         values_to_write = [df_prepared.columns.values.tolist()] + df_prepared.values.tolist()

#         body = {'values': values_to_write}
#         sheets_service.spreadsheets().values().update(
#             spreadsheetId=spreadsheet_id,
#             range=f"{first_sheet_title}!A1",
#             valueInputOption='USER_ENTERED',
#             body=body
#         ).execute()
        
#         return True

#     except HttpError as error:
#         st.error(f"An API error occurred while updating the Spreadsheet: {error}")
#         return False
#     except Exception as e:
#         st.error(f"An unexpected error occurred while updating the Spreadsheet: {e}")
#         return False

# def load_mcm_periods(drive_service):
#     """Loads the MCM periods configuration file from Google Drive."""
#     mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
#     if not mcm_periods_file_id:
#         if st.session_state.get('master_drive_folder_id'):
#             mcm_periods_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE,
#                                                           parent_id=st.session_state.master_drive_folder_id)
#             st.session_state.mcm_periods_drive_file_id = mcm_periods_file_id
#         else:
#             return {}

#     if mcm_periods_file_id:
#         try:
#             request = drive_service.files().get_media(fileId=mcm_periods_file_id)
#             fh = BytesIO()
#             downloader = MediaIoBaseDownload(fh, request)
#             done = False
#             while not done:
#                 status, done = downloader.next_chunk()
#             fh.seek(0)
#             return json.load(fh)
#         except HttpError as error:
#             if error.resp.status == 404:
#                 st.session_state.mcm_periods_drive_file_id = None
#             else:
#                 st.error(f"Error loading '{MCM_PERIODS_FILENAME_ON_DRIVE}' from Drive: {error}")
#             return {}
#         except json.JSONDecodeError:
#             st.error(f"Error decoding JSON from '{MCM_PERIODS_FILENAME_ON_DRIVE}'. File might be corrupted.")
#             return {}
#         except Exception as e:
#             st.error(f"Unexpected error loading '{MCM_PERIODS_FILENAME_ON_DRIVE}': {e}")
#             return {}
#     return {}

# def save_mcm_periods(drive_service, periods_data):
#     """Saves the MCM periods configuration file to Google Drive."""
#     master_folder_id = st.session_state.get('master_drive_folder_id')
#     if not master_folder_id:
#         st.error("Master Drive folder ID not set. Cannot save MCM periods configuration.")
#         return False

#     mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
#     file_content = json.dumps(periods_data, indent=4).encode('utf-8')
#     fh = BytesIO(file_content)
#     media_body = MediaIoBaseUpload(fh, mimetype='application/json', resumable=True)

#     try:
#         if mcm_periods_file_id:
#             file_metadata_update = {'name': MCM_PERIODS_FILENAME_ON_DRIVE}
#             drive_service.files().update(
#                 fileId=mcm_periods_file_id,
#                 body=file_metadata_update,
#                 media_body=media_body,
#                 fields='id, name'
#             ).execute()
#         else:
#             file_metadata_create = {'name': MCM_PERIODS_FILENAME_ON_DRIVE, 'parents': [master_folder_id]}
#             new_file = drive_service.files().create(
#                 body=file_metadata_create,
#                 media_body=media_body,
#                 fields='id, name'
#             ).execute()
#             st.session_state.mcm_periods_drive_file_id = new_file.get('id')
#         return True
#     except HttpError as error:
#         st.error(f"Error saving '{MCM_PERIODS_FILENAME_ON_DRIVE}' to Drive: {error}")
#         return False
#     except Exception as e:
#         st.error(f"Unexpected error saving '{MCM_PERIODS_FILENAME_ON_DRIVE}': {e}")
#         return False

# def append_to_spreadsheet(sheets_service, spreadsheet_id, values_to_append):
#     """Appends rows to a spreadsheet."""
#     try:
#         body = {'values': values_to_append}
#         sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
#         sheets = sheet_metadata.get('sheets', '')
#         first_sheet_title = sheets[0].get("properties", {}).get("title", "Sheet1")

#         append_result = sheets_service.spreadsheets().values().append(
#             spreadsheetId=spreadsheet_id,
#             range=f"{first_sheet_title}!A1",
#             valueInputOption='USER_ENTERED',
#             insertDataOption='INSERT_ROWS',
#             body=body
#         ).execute()
#         return append_result
#     except HttpError as error:
#         st.error(f"An error occurred appending to Spreadsheet: {error}")
#         return None
#     except Exception as e:
#         st.error(f"Unexpected error appending to Spreadsheet: {e}")
#         return None

# def delete_spreadsheet_rows(sheets_service, spreadsheet_id, sheet_id_gid, row_indices_to_delete):
#     """Deletes specific rows from a sheet."""
#     if not row_indices_to_delete:
#         return True
#     requests = []
#     # Sort in descending order to avoid index shifting issues during deletion
#     for data_row_index in sorted(row_indices_to_delete, reverse=True):
#         # The API uses 0-based index. If data starts at row 2 (index 1) after the header,
#         # the sheet row index for the API is data_row_index + 1.
#         sheet_row_start_index = data_row_index + 1
#         requests.append({
#             "deleteDimension": {
#                 "range": {
#                     "sheetId": sheet_id_gid,
#                     "dimension": "ROWS",
#                     "startIndex": sheet_row_start_index,
#                     "endIndex": sheet_row_start_index + 1
#                 }
#             }
#         })
#     if requests:
#         try:
#             body = {'requests': requests}
#             sheets_service.spreadsheets().batchUpdate(
#                 spreadsheetId=spreadsheet_id, body=body).execute()
#             return True
#         except HttpError as error:
#             st.error(f"An error occurred deleting rows from Spreadsheet: {error}")
#             return False
#         except Exception as e:
#             st.error(f"Unexpected error deleting rows: {e}")
#             return False
#     return True

# # def create_spreadsheet(sheets_service, drive_service, title, parent_folder_id=None):
# #     """Creates a spreadsheet and moves it to a specific folder with better error handling."""
# #     try:
# #         # Step 1: Create spreadsheet in root first (this usually works)
# #         spreadsheet_body = {'properties': {'title': title}}
# #         spreadsheet = sheets_service.spreadsheets().create(
# #             body=spreadsheet_body,
# #             fields='spreadsheetId,spreadsheetUrl'
# #         ).execute()
# #         spreadsheet_id = spreadsheet.get('spreadsheetId')
        
# #         if not spreadsheet_id:
# #             st.error("Failed to create spreadsheet - no ID returned")
# #             return None, None

# #         # Step 2: Try to move to target folder if specified
# #         if spreadsheet_id and drive_service and parent_folder_id:
# #             try:
# #                 # Get current parents
# #                 file = drive_service.files().get(fileId=spreadsheet_id, fields='parents').execute()
# #                 previous_parents = ",".join(file.get('parents', []))
                
# #                 # Move to target folder
# #                 drive_service.files().update(
# #                     fileId=spreadsheet_id,
# #                     addParents=parent_folder_id,
# #                     removeParents=previous_parents,
# #                     fields='id, parents'
# #                 ).execute()
# #                 st.success(f"✅ Spreadsheet '{title}' created and moved to target folder")
                
# #             except HttpError as move_error:
# #                 st.warning(f"⚠️ Spreadsheet created but couldn't move to folder: {move_error}")
# #                 st.info("The spreadsheet was created in your root Drive. You can manually move it if needed.")
# #                 # Don't fail completely - return the spreadsheet ID anyway
                
# #         return spreadsheet_id, spreadsheet.get('spreadsheetUrl')
        
# #     except HttpError as error:
# #         if error.resp.status == 403:
# #             st.error("❌ Permission denied. Please check service account permissions:")
# #             st.error("1. Service account needs 'Editor' access to your Google Drive")
# #             st.error("2. Service account needs Google Sheets API enabled")
# #             st.error("3. Try sharing your parent folder with the service account email")
# #         else:
# #             st.error(f"HTTP Error creating Spreadsheet: {error}")
# #         return None, None
# #     except Exception as e:
# #         st.error(f"Unexpected error creating Spreadsheet: {e}")
# #         return None, None

# def create_spreadsheet(sheets_service, drive_service, title, parent_folder_id=None):
#     """Creates a spreadsheet using Drive API (which is working) instead of Sheets API."""
#     try:
#         # Use Drive API to create the spreadsheet file
#         file_metadata = {
#             'name': title,
#             'mimeType': 'application/vnd.google-apps.spreadsheet'
#         }
        
#         if parent_folder_id:
#             file_metadata['parents'] = [parent_folder_id]
        
#         # Create using Drive API (this is working for you)
#         file = drive_service.files().create(
#             body=file_metadata,
#             fields='id, webViewLink, name'
#         ).execute()
        
#         spreadsheet_id = file.get('id')
        
#         if spreadsheet_id:
#             # Generate the correct spreadsheet URL
#             spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
#             st.success(f"✅ Spreadsheet '{title}' created successfully using Drive API")
#             return spreadsheet_id, spreadsheet_url
#         else:
#             raise Exception("No spreadsheet ID returned from Drive API")
            
#     except HttpError as error:
#         st.error(f"Drive API Error creating spreadsheet: {error}")
#         return None, None
#     except Exception as e:
#         st.error(f"Unexpected error creating spreadsheet: {e}")
#         return None, None
# def find_or_create_log_sheet(drive_service, sheets_service, parent_folder_id):
#     """Finds the log sheet or creates it if it doesn't exist."""
#     log_sheet_name = LOG_SHEET_FILENAME_ON_DRIVE
#     log_sheet_id = find_drive_item_by_name(drive_service, log_sheet_name,
#                                            mime_type='application/vnd.google-apps.spreadsheet',
#                                            parent_id=parent_folder_id)
#     if log_sheet_id:
#         return log_sheet_id
    
#     st.info(f"Log sheet '{log_sheet_name}' not found. Creating it...")
#     spreadsheet_id, _ = create_spreadsheet(sheets_service, drive_service, log_sheet_name, parent_folder_id=parent_folder_id)
    
#     if spreadsheet_id:
#         header = [['Timestamp', 'Username', 'Role']]
#         body = {'values': header}
#         try:
#             sheets_service.spreadsheets().values().append(
#                 spreadsheetId=spreadsheet_id, range='Sheet1!A1',
#                 valueInputOption='USER_ENTERED', body=body
#             ).execute()
#             st.success(f"Log sheet '{log_sheet_name}' created successfully.")
#         except HttpError as error:
#             st.error(f"Failed to write header to new log sheet: {error}")
#             return None
#         return spreadsheet_id
#     else:
#         st.error(f"Fatal: Failed to create log sheet '{log_sheet_name}'. Logging will be disabled.")
#         return None

# def log_activity(sheets_service, log_sheet_id, username, role):
#     """Appends a login activity record to the specified log sheet."""
#     if not log_sheet_id:
#         st.warning("Log Sheet ID is not available. Skipping activity logging.")
#         return False
    
#     try:
#         timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#         values = [[timestamp, username, role]]
#         body = {'values': values}
        
#         sheets_service.spreadsheets().values().append(
#             spreadsheetId=log_sheet_id,
#             range='Sheet1!A1',
#             valueInputOption='USER_ENTERED',
#             insertDataOption='INSERT_ROWS',
#             body=body
#         ).execute()
#         return True
#     except HttpError as error:
#         st.error(f"An error occurred while logging activity: {error}")
#         return False
#     except Exception as e:
#         st.error(f"An unexpected error occurred during logging: {e}")
#         return False

# def find_or_create_spreadsheet(drive_service, sheets_service, sheet_name, parent_folder_id):
#     """Finds a spreadsheet by name or creates it with a header if it doesn't exist."""
#     sheet_id = find_drive_item_by_name(drive_service, sheet_name,
#                                        mime_type='application/vnd.google-apps.spreadsheet',
#                                        parent_id=parent_folder_id)
#     if sheet_id:
#         return sheet_id

#     st.info(f"Spreadsheet '{sheet_name}' not found. Creating it...")
#     sheet_id, _ = create_spreadsheet(sheets_service, drive_service, sheet_name, parent_folder_id=parent_folder_id)
    
#     if sheet_id:
#         header = []
#         if sheet_name == SMART_AUDIT_MASTER_DB_SHEET_NAME:
#             header = [[
#                 "GSTIN", "Trade Name", "Category", "Allocated Audit Group Number", 
#                 "Allocated Circle", "Financial Year", "Allocated Date", "Uploaded Date", 
#                 "Office Order PDF Path", "Reassigned Flag", "Old Group Number", "Old Circle Number"
#             ]]
#         elif sheet_name == LOG_SHEET_FILENAME_ON_DRIVE:
#              header = [['Timestamp', 'Username', 'Role']]
        
#         if header:
#             body = {'values': header}
#             try:
#                 sheets_service.spreadsheets().values().append(
#                     spreadsheetId=sheet_id, range='Sheet1!A1',
#                     valueInputOption='USER_ENTERED', body=body
#                 ).execute()
#                 st.success(f"Spreadsheet '{sheet_name}' created successfully with headers.")
#             except HttpError as error:
#                 st.error(f"Failed to write header to new spreadsheet: {error}")
#                 return None
#         return sheet_id
#     else:
#         st.error(f"Fatal: Failed to create spreadsheet '{sheet_name}'.")
#         return None

# def read_from_spreadsheet(sheets_service, spreadsheet_id, sheet_name="Sheet1"):
#     """Reads an entire sheet into a pandas DataFrame, handling varying column counts."""
#     try:
#         result = sheets_service.spreadsheets().values().get(
#             spreadsheetId=spreadsheet_id,
#             range=sheet_name
#         ).execute()
#         values = result.get('values', [])

#         if not values:
#             return pd.DataFrame()

#         header = values[0]
#         data = values[1:]
        
#         if not data:
#             return pd.DataFrame(columns=header)

#         num_cols = len(header)
#         processed_data = []
#         for row in data:
#             new_row = list(row)
#             if len(new_row) < num_cols:
#                 new_row.extend([None] * (num_cols - len(new_row)))
#             elif len(new_row) > num_cols:
#                 new_row = new_row[:num_cols]
#             processed_data.append(new_row)

#         df = pd.DataFrame(processed_data, columns=header)
#         return df

#     except HttpError as error:
#         st.error(f"An API error occurred reading from Spreadsheet: {error}")
#         return pd.DataFrame()
#     except Exception as e:
#         st.error(f"An unexpected error occurred while reading the Spreadsheet: {e}")
#         return pd.DataFrame()

# def update_spreadsheet_from_df(sheets_service, spreadsheet_id, df_to_write):
#     """Clears a sheet and updates it with data from a pandas DataFrame."""
#     try:
#         sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
#         first_sheet_title = sheet_metadata['sheets'][0]['properties']['title']

#         sheets_service.spreadsheets().values().clear(
#             spreadsheetId=spreadsheet_id,
#             range=first_sheet_title
#         ).execute()

#         df_prepared = df_to_write.fillna('')
#         values_to_write = [df_prepared.columns.values.tolist()] + df_prepared.values.tolist()

#         body = {'values': values_to_write}
#         sheets_service.spreadsheets().values().update(
#             spreadsheetId=spreadsheet_id,
#             range=f"{first_sheet_title}!A1",
#             valueInputOption='USER_ENTERED',
#             body=body
#         ).execute()
        
#         return True

#     except HttpError as error:
#         st.error(f"An API error occurred while updating the Spreadsheet: {error}")
#         return False
#     except Exception as e:
#         st.error(f"An unexpected error occurred while updating the Spreadsheet: {e}")
#         return False

# def load_mcm_periods(drive_service):
#     """Loads the MCM periods configuration file from Google Drive."""
#     mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
#     if not mcm_periods_file_id:
#         if st.session_state.get('master_drive_folder_id'):
#             mcm_periods_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE,
#                                                           parent_id=st.session_state.master_drive_folder_id)
#             st.session_state.mcm_periods_drive_file_id = mcm_periods_file_id
#         else:
#             return {}

#     if mcm_periods_file_id:
#         try:
#             request = drive_service.files().get_media(fileId=mcm_periods_file_id)
#             fh = BytesIO()
#             downloader = MediaIoBaseDownload(fh, request)
#             done = False
#             while not done:
#                 status, done = downloader.next_chunk()
#             fh.seek(0)
#             return json.load(fh)
#         except HttpError as error:
#             if error.resp.status == 404:
#                 st.session_state.mcm_periods_drive_file_id = None
#             else:
#                 st.error(f"Error loading '{MCM_PERIODS_FILENAME_ON_DRIVE}' from Drive: {error}")
#             return {}
#         except json.JSONDecodeError:
#             st.error(f"Error decoding JSON from '{MCM_PERIODS_FILENAME_ON_DRIVE}'. File might be corrupted.")
#             return {}
#         except Exception as e:
#             st.error(f"Unexpected error loading '{MCM_PERIODS_FILENAME_ON_DRIVE}': {e}")
#             return {}
#     return {}

# def save_mcm_periods(drive_service, periods_data):
#     """Saves the MCM periods configuration file to Google Drive."""
#     master_folder_id = st.session_state.get('master_drive_folder_id')
#     if not master_folder_id:
#         st.error("Master Drive folder ID not set. Cannot save MCM periods configuration.")
#         return False

#     mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
#     file_content = json.dumps(periods_data, indent=4).encode('utf-8')
#     fh = BytesIO(file_content)
#     media_body = MediaIoBaseUpload(fh, mimetype='application/json', resumable=True)

#     try:
#         if mcm_periods_file_id:
#             file_metadata_update = {'name': MCM_PERIODS_FILENAME_ON_DRIVE}
#             drive_service.files().update(
#                 fileId=mcm_periods_file_id,
#                 body=file_metadata_update,
#                 media_body=media_body,
#                 fields='id, name'
#             ).execute()
#         else:
#             file_metadata_create = {'name': MCM_PERIODS_FILENAME_ON_DRIVE, 'parents': [master_folder_id]}
#             new_file = drive_service.files().create(
#                 body=file_metadata_create,
#                 media_body=media_body,
#                 fields='id, name'
#             ).execute()
#             st.session_state.mcm_periods_drive_file_id = new_file.get('id')
#         return True
#     except HttpError as error:
#         st.error(f"Error saving '{MCM_PERIODS_FILENAME_ON_DRIVE}' to Drive: {error}")
#         return False
#     except Exception as e:
#         st.error(f"Unexpected error saving '{MCM_PERIODS_FILENAME_ON_DRIVE}': {e}")
#         return False

# def append_to_spreadsheet(sheets_service, spreadsheet_id, values_to_append):
#     """Appends rows to a spreadsheet."""
#     try:
#         body = {'values': values_to_append}
#         sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
#         sheets = sheet_metadata.get('sheets', '')
#         first_sheet_title = sheets[0].get("properties", {}).get("title", "Sheet1")

#         append_result = sheets_service.spreadsheets().values().append(
#             spreadsheetId=spreadsheet_id,
#             range=f"{first_sheet_title}!A1",
#             valueInputOption='USER_ENTERED',
#             insertDataOption='INSERT_ROWS',
#             body=body
#         ).execute()
#         return append_result
#     except HttpError as error:
#         st.error(f"An error occurred appending to Spreadsheet: {error}")
#         return None
#     except Exception as e:
#         st.error(f"Unexpected error appending to Spreadsheet: {e}")
#         return None

# def delete_spreadsheet_rows(sheets_service, spreadsheet_id, sheet_id_gid, row_indices_to_delete):
#     """Deletes specific rows from a sheet."""
#     if not row_indices_to_delete:
#         return True
#     requests = []
#     # Sort in descending order to avoid index shifting issues during deletion
#     for data_row_index in sorted(row_indices_to_delete, reverse=True):
#         # The API uses 0-based index. If data starts at row 2 (index 1) after the header,
#         # the sheet row index for the API is data_row_index + 1.
#         sheet_row_start_index = data_row_index + 1
#         requests.append({
#             "deleteDimension": {
#                 "range": {
#                     "sheetId": sheet_id_gid,
#                     "dimension": "ROWS",
#                     "startIndex": sheet_row_start_index,
#                     "endIndex": sheet_row_start_index + 1
#                 }
#             }
#         })
#     if requests:
#         try:
#             body = {'requests': requests}
#             sheets_service.spreadsheets().batchUpdate(
#                 spreadsheetId=spreadsheet_id, body=body).execute()
#             return True
#         except HttpError as error:
#             st.error(f"An error occurred deleting rows from Spreadsheet: {error}")
#             return False
#         except Exception as e:
#             st.error(f"Unexpected error deleting rows: {e}")
#             return False
#     return True# # google_utils.py
# def test_permissions_debug(drive_service, sheets_service):
#     """Test function to debug permissions issues"""
#     st.subheader("🔍 Permission Diagnostic Test")
    
#     if st.button("Run Permission Test"):
#         results = []
        
#         # Test 1: Basic Drive access
#         try:
#             drive_service.files().list(pageSize=1).execute()
#             results.append("✅ Basic Drive access: OK")
#         except Exception as e:
#             results.append(f"❌ Basic Drive access: {e}")
        
#         # Test 2: Parent folder access
#         try:
#             folder_info = drive_service.files().get(fileId=PARENT_FOLDER_ID).execute()
#             results.append(f"✅ Parent folder access: OK - {folder_info.get('name')}")
#         except Exception as e:
#             results.append(f"❌ Parent folder access: {e}")
        
#         # Test 3: Create spreadsheet in root
#         try:
#             test_sheet = sheets_service.spreadsheets().create(
#                 body={'properties': {'title': 'TEST_PERMISSIONS_DELETE_ME'}}
#             ).execute()
#             sheet_id = test_sheet.get('spreadsheetId')
#             results.append("✅ Create spreadsheet in root: OK")
            
#             # Clean up test sheet
#             try:
#                 drive_service.files().delete(fileId=sheet_id).execute()
#                 results.append("✅ Cleanup test sheet: OK")
#             except:
#                 results.append(f"⚠️ Test sheet created but not cleaned up: {sheet_id}")
                
#         except Exception as e:
#             results.append(f"❌ Create spreadsheet in root: {e}")
        
#         # Test 4: Create folder in parent
#         try:
#             test_folder = drive_service.files().create(
#                 body={
#                     'name': 'TEST_PERMISSIONS_DELETE_ME',
#                     'mimeType': 'application/vnd.google-apps.folder',
#                     'parents': [PARENT_FOLDER_ID]
#                 }
#             ).execute()
#             folder_id = test_folder.get('id')
#             results.append("✅ Create folder in parent: OK")
            
#             # Clean up test folder
#             try:
#                 drive_service.files().delete(fileId=folder_id).execute()
#                 results.append("✅ Cleanup test folder: OK")
#             except:
#                 results.append(f"⚠️ Test folder created but not cleaned up: {folder_id}")
                
#         except Exception as e:
#             results.append(f"❌ Create folder in parent: {e}")
        
#         # Display results
#         for result in results:
#             if "✅" in result:
#                 st.success(result)
#             elif "❌" in result:
#                 st.error(result)
#             else:
#                 st.warning(result)
        
#         # Show service account info if available
#         try:
#             about = drive_service.about().get(fields='user').execute()
#             user_info = about.get('user', {})
#             st.info(f"Service account email: {user_info.get('emailAddress', 'Unknown')}")
#         except:
#             st.warning("Could not retrieve service account email")
# def test_root_spreadsheet_creation(sheets_service, drive_service):
#     """Test creating spreadsheet in root Drive"""
#     st.subheader("🧪 Test Spreadsheet Creation in Root")
    
#     if st.button("Test Create in Root Drive"):
#         test_title = f"TEST_ROOT_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
#         with st.spinner("Testing..."):
#             try:
#                 # Create without any parent folder
#                 spreadsheet_body = {'properties': {'title': test_title}}
#                 spreadsheet = sheets_service.spreadsheets().create(
#                     body=spreadsheet_body,
#                     fields='spreadsheetId,spreadsheetUrl'
#                 ).execute()
                
#                 spreadsheet_id = spreadsheet.get('spreadsheetId')
#                 spreadsheet_url = spreadsheet.get('spreadsheetUrl')
                
#                 if spreadsheet_id:
#                     st.success("✅ SUCCESS! Spreadsheet created in root Drive")
#                     st.info(f"**ID:** {spreadsheet_id}")
#                     st.info(f"**URL:** {spreadsheet_url}")
                    
#                     # Clean up test file
#                     try:
#                         drive_service.files().delete(fileId=spreadsheet_id).execute()
#                         st.success("✅ Test file cleaned up")
#                     except:
#                         st.warning("⚠️ Test file created but not cleaned up")
#                         st.info("You can manually delete it from your Drive")
#                 else:
#                     st.error("❌ No spreadsheet ID returned")
                    
#             except Exception as e:
#                 st.error(f"❌ Test failed: {e}")
# # from datetime import datetime 
# # import streamlit as st
# # import os
# # import json
# # from io import BytesIO
# # import pandas as pd
# # import math

# # from google.oauth2 import service_account
# # from googleapiclient.discovery import build
# # from googleapiclient.errors import HttpError
# # from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload

# # from config import SCOPES, MASTER_DRIVE_FOLDER_NAME, MCM_PERIODS_FILENAME_ON_DRIVE, LOG_SHEET_FILENAME_ON_DRIVE, SMART_AUDIT_MASTER_DB_SHEET_NAME, PARENT_FOLDER_ID

# # def get_google_services():
# #     """Initializes and returns the Google Drive and Sheets service objects."""
# #     creds = None
# #     try:
# #         creds_dict = st.secrets["google_credentials"]
# #         creds = service_account.Credentials.from_service_account_info(
# #             creds_dict, scopes=SCOPES
# #         )
# #     except KeyError:
# #         st.error("Google credentials not found in Streamlit secrets. Ensure 'google_credentials' are set.")
# #         return None, None
# #     except Exception as e:
# #         st.error(f"Failed to load service account credentials from secrets: {e}")
# #         return None, None

# #     if not creds: return None, None

# #     try:
# #         drive_service = build('drive', 'v3', credentials=creds)
# #         sheets_service = build('sheets', 'v4', credentials=creds)
# #         return drive_service, sheets_service
# #     except HttpError as error:
# #         st.error(f"An error occurred initializing Google services: {error}")
# #         return None, None
# #     except Exception as e:
# #         st.error(f"An unexpected error with Google services: {e}")
# #         return None, None

# # def find_drive_item_by_name(drive_service, name, mime_type=None, parent_id=None):
# #     """Finds a file or folder by name in regular Drive (no shared drive)."""
# #     query = f"name = '{name}' and trashed = false"
# #     if mime_type:
# #         query += f" and mimeType = '{mime_type}'"
# #     if parent_id:
# #         query += f" and '{parent_id}' in parents"
    
# #     try:
# #         response = drive_service.files().list(
# #             q=query,
# #             spaces='drive',
# #             fields='files(id, name)'
# #         ).execute()
        
# #         items = response.get('files', [])
# #         if items:
# #             return items[0].get('id')
# #     except HttpError as error:
# #         st.warning(f"Error searching for '{name}' in Drive: {error}. This might be okay if the item is to be created.")
# #     except Exception as e:
# #         st.warning(f"Unexpected error searching for '{name}' in Drive: {e}")
# #     return None

# # def create_drive_folder(drive_service, folder_name, parent_id=None):
# #     """Creates a folder in regular Drive (no shared drive support needed)."""
# #     try:
# #         file_metadata = {
# #             'name': folder_name,
# #             'mimeType': 'application/vnd.google-apps.folder'
# #         }
# #         if parent_id:
# #             file_metadata['parents'] = [parent_id]

# #         folder = drive_service.files().create(
# #             body=file_metadata, 
# #             fields='id, webViewLink'
# #         ).execute()
# #         return folder.get('id'), folder.get('webViewLink')
# #     except HttpError as error:
# #         st.error(f"An error occurred creating Drive folder '{folder_name}': {error}")
# #         return None, None
# #     except Exception as e:
# #         st.error(f"Unexpected error creating Drive folder '{folder_name}': {e}")
# #         return None, None

# # def initialize_drive_structure(drive_service, sheets_service):
# #     """
# #     FIXED: Initialize using regular folder structure instead of shared drive.
# #     """
# #     if not PARENT_FOLDER_ID or "PASTE" in PARENT_FOLDER_ID:
# #         st.error("CRITICAL: `PARENT_FOLDER_ID` is not configured in `config.py`. Please follow the setup instructions.")
# #         return False

# #     # 1. Find or Create the Master Folder inside the Parent Folder
# #     master_id = st.session_state.get('master_drive_folder_id')
# #     if not master_id:
# #         master_id = find_drive_item_by_name(drive_service, MASTER_DRIVE_FOLDER_NAME,
# #                                             'application/vnd.google-apps.folder', 
# #                                             parent_id=PARENT_FOLDER_ID)
# #         if not master_id:
# #             st.info(f"Master folder '{MASTER_DRIVE_FOLDER_NAME}' not found in parent folder, creating it...")
# #             master_id, _ = create_drive_folder(drive_service, MASTER_DRIVE_FOLDER_NAME, 
# #                                                parent_id=PARENT_FOLDER_ID)
# #             if not master_id:
# #                 st.error(f"Fatal: Failed to create master folder '{MASTER_DRIVE_FOLDER_NAME}'. Cannot proceed.")
# #                 return False
# #         st.session_state.master_drive_folder_id = master_id

# #     if not st.session_state.master_drive_folder_id:
# #         st.error("Master Drive folder ID could not be established. Cannot proceed.")
# #         return False

# #     # 2. Find or Create the Log Sheet inside the Master Folder
# #     if not st.session_state.get('log_sheet_id'):
# #         log_sheet_id = find_or_create_log_sheet(drive_service, sheets_service, st.session_state.master_drive_folder_id)
# #         if not log_sheet_id:
# #             st.error("Failed to create the application log sheet. Logging will be disabled.")
# #         st.session_state.log_sheet_id = log_sheet_id

# #     # 3. Find or Create the MCM Periods Config File inside the Master Folder
# #     if not st.session_state.get('mcm_periods_drive_file_id'):
# #         mcm_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE, 
# #                                               parent_id=st.session_state.master_drive_folder_id)
# #         if not mcm_file_id:
# #             st.info(f"MCM Periods config file '{MCM_PERIODS_FILENAME_ON_DRIVE}' not found, creating it...")
# #             # Check if we have May 2024 data to migrate
# #             may_data = check_and_migrate_may_data(drive_service, sheets_service)
# #             save_mcm_periods(drive_service, may_data) # Create config file with May data if found
# #         else:
# #             st.session_state.mcm_periods_drive_file_id = mcm_file_id

# #     return True

# # def check_and_migrate_may_data(drive_service, sheets_service):
# #     """
# #     Check if the May 2024 spreadsheet exists and create MCM periods config to include it.
# #     """
# #     may_spreadsheet_id = "1-usWIYB-AfAelCjmHdY7H2dVXAydXHmW3iYQA-zFPW4"
    
# #     try:
# #         # Test if we can access the May spreadsheet
# #         result = sheets_service.spreadsheets().get(spreadsheetId=may_spreadsheet_id).execute()
# #         if result:
# #             st.success("Found existing May 2024 data! Adding it to MCM periods configuration.")
# #             # Create MCM periods config with May data
# #             mcm_periods = {
# #                 "2024-05": {
# #                     "month_name": "May",
# #                     "year": 2024,
# #                     "spreadsheet_id": may_spreadsheet_id,
# #                     "folder_id": None,  # Will be set later if needed
# #                     "created_date": "2024-05-01"  # Placeholder date
# #                 }
# #             }
# #             return mcm_periods
# #     except Exception as e:
# #         st.info(f"May 2024 spreadsheet not accessible or doesn't exist: {e}")
    
# #     return {}  # Return empty config if May data not found

# # def upload_to_drive(drive_service, file_content_or_path, folder_id, filename_on_drive):
# #     """Uploads a file to a specific folder (regular Drive)."""
# #     try:
# #         file_metadata = {'name': filename_on_drive, 'parents': [folder_id]}
# #         media_body = None

# #         if isinstance(file_content_or_path, bytes):
# #             fh = BytesIO(file_content_or_path)
# #             media_body = MediaIoBaseUpload(fh, mimetype='application/pdf', resumable=True)
# #         else:
# #             st.error(f"Unsupported file content type for Google Drive upload: {type(file_content_or_path)}")
# #             return None, None

# #         request = drive_service.files().create(
# #             body=file_metadata,
# #             media_body=media_body,
# #             fields='id, webViewLink'
# #         )
# #         file = request.execute()
# #         return file.get('id'), file.get('webViewLink')
# #     except HttpError as error:
# #         st.error(f"An API error occurred uploading to Drive: {error}")
# #         return None, None
# #     except Exception as e:
# #         st.error(f"An unexpected error in upload_to_drive: {e}")
# #         return None, None

# # def create_spreadsheet(sheets_service, drive_service, title, parent_folder_id=None):
# #     """Creates a spreadsheet and moves it to a specific folder (regular Drive)."""
# #     try:
# #         spreadsheet_body = {'properties': {'title': title}}
# #         spreadsheet = sheets_service.spreadsheets().create(body=spreadsheet_body,
# #                                                            fields='spreadsheetId,spreadsheetUrl').execute()
# #         spreadsheet_id = spreadsheet.get('spreadsheetId')

# #         if spreadsheet_id and drive_service and parent_folder_id:
# #             file = drive_service.files().get(fileId=spreadsheet_id, fields='parents').execute()
# #             previous_parents = ",".join(file.get('parents'))
# #             drive_service.files().update(
# #                 fileId=spreadsheet_id,
# #                 addParents=parent_folder_id,
# #                 removeParents=previous_parents,
# #                 fields='id, parents'
# #             ).execute()
# #         return spreadsheet_id, spreadsheet.get('spreadsheetUrl')
# #     except HttpError as error:
# #         st.error(f"An error occurred creating Spreadsheet: {error}")
# #         return None, None
# #     except Exception as e:
# #         st.error(f"An unexpected error occurred creating Spreadsheet: {e}")
# #         return None, None

# # def find_or_create_log_sheet(drive_service, sheets_service, parent_folder_id):
# #     """Finds the log sheet or creates it if it doesn't exist."""
# #     log_sheet_name = LOG_SHEET_FILENAME_ON_DRIVE
# #     log_sheet_id = find_drive_item_by_name(drive_service, log_sheet_name,
# #                                            mime_type='application/vnd.google-apps.spreadsheet',
# #                                            parent_id=parent_folder_id)
# #     if log_sheet_id:
# #         return log_sheet_id
    
# #     st.info(f"Log sheet '{log_sheet_name}' not found. Creating it...")
# #     spreadsheet_id, _ = create_spreadsheet(sheets_service, drive_service, log_sheet_name, parent_folder_id=parent_folder_id)
    
# #     if spreadsheet_id:
# #         header = [['Timestamp', 'Username', 'Role']]
# #         body = {'values': header}
# #         try:
# #             sheets_service.spreadsheets().values().append(
# #                 spreadsheetId=spreadsheet_id, range='Sheet1!A1',
# #                 valueInputOption='USER_ENTERED', body=body
# #             ).execute()
# #             st.success(f"Log sheet '{log_sheet_name}' created successfully.")
# #         except HttpError as error:
# #             st.error(f"Failed to write header to new log sheet: {error}")
# #             return None
# #         return spreadsheet_id
# #     else:
# #         st.error(f"Fatal: Failed to create log sheet '{log_sheet_name}'. Logging will be disabled.")
# #         return None

# # def log_activity(sheets_service, log_sheet_id, username, role):
# #     """Appends a login activity record to the specified log sheet."""
# #     if not log_sheet_id:
# #         st.warning("Log Sheet ID is not available. Skipping activity logging.")
# #         return False
    
# #     try:
# #         timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
# #         values = [[timestamp, username, role]]
# #         body = {'values': values}
        
# #         sheets_service.spreadsheets().values().append(
# #             spreadsheetId=log_sheet_id,
# #             range='Sheet1!A1',
# #             valueInputOption='USER_ENTERED',
# #             insertDataOption='INSERT_ROWS',
# #             body=body
# #         ).execute()
# #         return True
# #     except HttpError as error:
# #         st.error(f"An error occurred while logging activity: {error}")
# #         return False
# #     except Exception as e:
# #         st.error(f"An unexpected error occurred during logging: {e}")
# #         return False

# # def find_or_create_spreadsheet(drive_service, sheets_service, sheet_name, parent_folder_id):
# #     """Finds a spreadsheet by name or creates it with a header if it doesn't exist."""
# #     sheet_id = find_drive_item_by_name(drive_service, sheet_name,
# #                                        mime_type='application/vnd.google-apps.spreadsheet',
# #                                        parent_id=parent_folder_id)
# #     if sheet_id:
# #         return sheet_id

# #     st.info(f"Spreadsheet '{sheet_name}' not found. Creating it...")
# #     sheet_id, _ = create_spreadsheet(sheets_service, drive_service, sheet_name, parent_folder_id=parent_folder_id)
    
# #     if sheet_id:
# #         header = []
# #         if sheet_name == SMART_AUDIT_MASTER_DB_SHEET_NAME:
# #             header = [[
# #                 "GSTIN", "Trade Name", "Category", "Allocated Audit Group Number", 
# #                 "Allocated Circle", "Financial Year", "Allocated Date", "Uploaded Date", 
# #                 "Office Order PDF Path", "Reassigned Flag", "Old Group Number", "Old Circle Number"
# #             ]]
# #         elif sheet_name == LOG_SHEET_FILENAME_ON_DRIVE:
# #              header = [['Timestamp', 'Username', 'Role']]
        
# #         if header:
# #             body = {'values': header}
# #             try:
# #                 sheets_service.spreadsheets().values().append(
# #                     spreadsheetId=sheet_id, range='Sheet1!A1',
# #                     valueInputOption='USER_ENTERED', body=body
# #                 ).execute()
# #                 st.success(f"Spreadsheet '{sheet_name}' created successfully with headers.")
# #             except HttpError as error:
# #                 st.error(f"Failed to write header to new spreadsheet: {error}")
# #                 return None
# #         return sheet_id
# #     else:
# #         st.error(f"Fatal: Failed to create spreadsheet '{sheet_name}'.")
# #         return None

# # def read_from_spreadsheet(sheets_service, spreadsheet_id, sheet_name="Sheet1"):
# #     """Reads an entire sheet into a pandas DataFrame, handling varying column counts."""
# #     try:
# #         result = sheets_service.spreadsheets().values().get(
# #             spreadsheetId=spreadsheet_id,
# #             range=sheet_name
# #         ).execute()
# #         values = result.get('values', [])

# #         if not values:
# #             return pd.DataFrame()

# #         header = values[0]
# #         data = values[1:]
        
# #         if not data:
# #             return pd.DataFrame(columns=header)

# #         num_cols = len(header)
# #         processed_data = []
# #         for row in data:
# #             new_row = list(row)
# #             if len(new_row) < num_cols:
# #                 new_row.extend([None] * (num_cols - len(new_row)))
# #             elif len(new_row) > num_cols:
# #                 new_row = new_row[:num_cols]
# #             processed_data.append(new_row)

# #         df = pd.DataFrame(processed_data, columns=header)
# #         return df

# #     except HttpError as error:
# #         st.error(f"An API error occurred reading from Spreadsheet: {error}")
# #         return pd.DataFrame()
# #     except Exception as e:
# #         st.error(f"An unexpected error occurred while reading the Spreadsheet: {e}")
# #         return pd.DataFrame()

# # def update_spreadsheet_from_df(sheets_service, spreadsheet_id, df_to_write):
# #     """Clears a sheet and updates it with data from a pandas DataFrame."""
# #     try:
# #         sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
# #         first_sheet_title = sheet_metadata['sheets'][0]['properties']['title']

# #         sheets_service.spreadsheets().values().clear(
# #             spreadsheetId=spreadsheet_id,
# #             range=first_sheet_title
# #         ).execute()

# #         df_prepared = df_to_write.fillna('')
# #         values_to_write = [df_prepared.columns.values.tolist()] + df_prepared.values.tolist()

# #         body = {'values': values_to_write}
# #         sheets_service.spreadsheets().values().update(
# #             spreadsheetId=spreadsheet_id,
# #             range=f"{first_sheet_title}!A1",
# #             valueInputOption='USER_ENTERED',
# #             body=body
# #         ).execute()
        
# #         return True

# #     except HttpError as error:
# #         st.error(f"An API error occurred while updating the Spreadsheet: {error}")
# #         return False
# #     except Exception as e:
# #         st.error(f"An unexpected error occurred while updating the Spreadsheet: {e}")
# #         return False

# # def load_mcm_periods(drive_service):
# #     """Loads the MCM periods configuration file from Google Drive."""
# #     mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
# #     if not mcm_periods_file_id:
# #         if st.session_state.get('master_drive_folder_id'):
# #             mcm_periods_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE,
# #                                                           parent_id=st.session_state.master_drive_folder_id)
# #             st.session_state.mcm_periods_drive_file_id = mcm_periods_file_id
# #         else:
# #             return {}

# #     if mcm_periods_file_id:
# #         try:
# #             request = drive_service.files().get_media(fileId=mcm_periods_file_id)
# #             fh = BytesIO()
# #             downloader = MediaIoBaseDownload(fh, request)
# #             done = False
# #             while not done:
# #                 status, done = downloader.next_chunk()
# #             fh.seek(0)
# #             return json.load(fh)
# #         except HttpError as error:
# #             if error.resp.status == 404:
# #                 st.session_state.mcm_periods_drive_file_id = None
# #             else:
# #                 st.error(f"Error loading '{MCM_PERIODS_FILENAME_ON_DRIVE}' from Drive: {error}")
# #             return {}
# #         except json.JSONDecodeError:
# #             st.error(f"Error decoding JSON from '{MCM_PERIODS_FILENAME_ON_DRIVE}'. File might be corrupted.")
# #             return {}
# #         except Exception as e:
# #             st.error(f"Unexpected error loading '{MCM_PERIODS_FILENAME_ON_DRIVE}': {e}")
# #             return {}
# #     return {}

# # def save_mcm_periods(drive_service, periods_data):
# #     """Saves the MCM periods configuration file to Google Drive."""
# #     master_folder_id = st.session_state.get('master_drive_folder_id')
# #     if not master_folder_id:
# #         st.error("Master Drive folder ID not set. Cannot save MCM periods configuration.")
# #         return False

# #     mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
# #     file_content = json.dumps(periods_data, indent=4).encode('utf-8')
# #     fh = BytesIO(file_content)
# #     media_body = MediaIoBaseUpload(fh, mimetype='application/json', resumable=True)

# #     try:
# #         if mcm_periods_file_id:
# #             file_metadata_update = {'name': MCM_PERIODS_FILENAME_ON_DRIVE}
# #             drive_service.files().update(
# #                 fileId=mcm_periods_file_id,
# #                 body=file_metadata_update,
# #                 media_body=media_body,
# #                 fields='id, name'
# #             ).execute()
# #         else:
# #             file_metadata_create = {'name': MCM_PERIODS_FILENAME_ON_DRIVE, 'parents': [master_folder_id]}
# #             new_file = drive_service.files().create(
# #                 body=file_metadata_create,
# #                 media_body=media_body,
# #                 fields='id, name'
# #             ).execute()
# #             st.session_state.mcm_periods_drive_file_id = new_file.get('id')
# #         return True
# #     except HttpError as error:
# #         st.error(f"Error saving '{MCM_PERIODS_FILENAME_ON_DRIVE}' to Drive: {error}")
# #         return False
# #     except Exception as e:
# #         st.error(f"Unexpected error saving '{MCM_PERIODS_FILENAME_ON_DRIVE}': {e}")
# #         return False

# # def append_to_spreadsheet(sheets_service, spreadsheet_id, values_to_append):
# #     """Appends rows to a spreadsheet."""
# #     try:
# #         body = {'values': values_to_append}
# #         sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
# #         sheets = sheet_metadata.get('sheets', '')
# #         first_sheet_title = sheets[0].get("properties", {}).get("title", "Sheet1")

# #         append_result = sheets_service.spreadsheets().values().append(
# #             spreadsheetId=spreadsheet_id,
# #             range=f"{first_sheet_title}!A1",
# #             valueInputOption='USER_ENTERED',
# #             insertDataOption='INSERT_ROWS',
# #             body=body
# #         ).execute()
# #         return append_result
# #     except HttpError as error:
# #         st.error(f"An error occurred appending to Spreadsheet: {error}")
# #         return None
# #     except Exception as e:
# #         st.error(f"Unexpected error appending to Spreadsheet: {e}")
# #         return None

# # def delete_spreadsheet_rows(sheets_service, spreadsheet_id, sheet_id_gid, row_indices_to_delete):
# #     """Deletes specific rows from a sheet."""
# #     if not row_indices_to_delete:
# #         return True
# #     requests = []
# #     # Sort in descending order to avoid index shifting issues during deletion
# #     for data_row_index in sorted(row_indices_to_delete, reverse=True):
# #         # The API uses 0-based index. If data starts at row 2 (index 1) after the header,
# #         # the sheet row index for the API is data_row_index + 1.
# #         sheet_row_start_index = data_row_index + 1
# #         requests.append({
# #             "deleteDimension": {
# #                 "range": {
# #                     "sheetId": sheet_id_gid,
# #                     "dimension": "ROWS",
# #                     "startIndex": sheet_row_start_index,
# #                     "endIndex": sheet_row_start_index + 1
# #                 }
# #             }
# #         })
# #     if requests:
# #         try:
# #             body = {'requests': requests}
# #             sheets_service.spreadsheets().batchUpdate(
# #                 spreadsheetId=spreadsheet_id, body=body).execute()
# #             return True
# #         except HttpError as error:
# #             st.error(f"An error occurred deleting rows from Spreadsheet: {error}")
# #             return False
# #         except Exception as e:
# #             st.error(f"Unexpected error deleting rows: {e}")
# #             return False
# #     return True
# #     # # google_utils.py
# # # from datetime import datetime 
# # # import streamlit as st
# # # import os
# # # import json
# # # from io import BytesIO
# # # import pandas as pd
# # # import math

# # # from google.oauth2 import service_account
# # # from googleapiclient.discovery import build
# # # from googleapiclient.errors import HttpError
# # # from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload

# # # from config import SCOPES, MASTER_DRIVE_FOLDER_NAME, MCM_PERIODS_FILENAME_ON_DRIVE, LOG_SHEET_FILENAME_ON_DRIVE, SMART_AUDIT_MASTER_DB_SHEET_NAME, SHARED_DRIVE_ID

# # # def get_google_services():
# # #     """Initializes and returns the Google Drive and Sheets service objects."""
# # #     creds = None
# # #     try:
# # #         creds_dict = st.secrets["google_credentials"]
# # #         creds = service_account.Credentials.from_service_account_info(
# # #             creds_dict, scopes=SCOPES
# # #         )
# # #     except KeyError:
# # #         st.error("Google credentials not found in Streamlit secrets. Ensure 'google_credentials' are set.")
# # #         return None, None
# # #     except Exception as e:
# # #         st.error(f"Failed to load service account credentials from secrets: {e}")
# # #         return None, None

# # #     if not creds: return None, None

# # #     try:
# # #         drive_service = build('drive', 'v3', credentials=creds)
# # #         sheets_service = build('sheets', 'v4', credentials=creds)
# # #         return drive_service, sheets_service
# # #     except HttpError as error:
# # #         st.error(f"An error occurred initializing Google services: {error}")
# # #         return None, None
# # #     except Exception as e:
# # #         st.error(f"An unexpected error with Google services: {e}")
# # #         return None, None

# # # def find_drive_item_by_name(drive_service, name, mime_type=None, parent_id=None):
# # #     """Finds a file or folder by name, supporting Shared Drives."""
# # #     query = f"name = '{name}' and trashed = false"
# # #     if mime_type:
# # #         query += f" and mimeType = '{mime_type}'"
# # #     if parent_id:
# # #         query += f" and '{parent_id}' in parents"
    
# # #     try:
# # #        response = drive_service.files().list(
# # #             q=query,
# # #             # Add these parameters for Shared Drive support
# # #             corpora='drive',
# # #             driveId=SHARED_DRIVE_ID,
# # #             includeItemsFromAllDrives=True,
# # #             supportsAllDrives=True,
# # #             spaces='drive',
# # #             fields='files(id, name)'
# # #         ).execute()
# # #        items = response.get('files', [])
# # #        if items:
# # #             return items[0].get('id')
# # #     except HttpError as error:
# # #         st.warning(f"Error searching for '{name}' in Drive: {error}. This might be okay if the item is to be created.")
# # #     except Exception as e:
# # #         st.warning(f"Unexpected error searching for '{name}' in Drive: {e}")
# # #     return None

# # # def create_drive_folder(drive_service, folder_name, parent_id=None):
# # #     """Creates a folder, supporting Shared Drives."""
# # #     try:
# # #         file_metadata = {
# # #             'name': folder_name,
# # #             'mimeType': 'application/vnd.google-apps.folder'
# # #         }
# # #         if parent_id:
# # #             file_metadata['parents'] = [parent_id]

# # #         folder = drive_service.files().create(
# # #             body=file_metadata, 
# # #             fields='id, webViewLink',
# # #             supportsAllDrives=True
# # #         ).execute()
# # #         return folder.get('id'), folder.get('webViewLink')
# # #     except HttpError as error:
# # #         st.error(f"An error occurred creating Drive folder '{folder_name}': {error}")
# # #         return None, None
# # #     except Exception as e:
# # #         st.error(f"Unexpected error creating Drive folder '{folder_name}': {e}")
# # #         return None, None

# # # # def initialize_drive_structure(drive_service):
    
# # # #     """
# # # #     Initializes the entire application folder and file structure within the specified Shared Drive.
# # # #     This is the central point of control for ensuring all files are created in the correct location.
# # # #     """
# # # #     if not SHARED_DRIVE_ID or "PASTE" in SHARED_DRIVE_ID:
# # # #         st.error("CRITICAL: `SHARED_DRIVE_ID` is not configured in `config.py`. Please follow the setup instructions.")
# # # #         return False

# # # #     # 1. Find or Create the Master Folder inside the Shared Drive
# # # #     master_id = st.session_state.get('master_drive_folder_id')
# # # #     if not master_id:
# # # #         master_id = find_drive_item_by_name(drive_service, MASTER_DRIVE_FOLDER_NAME,
# # # #                                             'application/vnd.google-apps.folder', parent_id=SHARED_DRIVE_ID)
# # # #         if not master_id:
# # # #             st.info(f"Master folder '{MASTER_DRIVE_FOLDER_NAME}' not found in Shared Drive, creating it...")
# # # #             master_id, _ = create_drive_folder(drive_service, MASTER_DRIVE_FOLDER_NAME, parent_id=SHARED_DRIVE_ID)
# # # #             if not master_id:
# # # #                 st.error(f"Fatal: Failed to create master folder '{MASTER_DRIVE_FOLDER_NAME}'. Cannot proceed.")
# # # #                 return False
# # # #         st.session_state.master_drive_folder_id = master_id

# # # #     if not st.session_state.master_drive_folder_id:
# # # #         st.error("Master Drive folder ID could not be established. Cannot proceed.")
# # # #         return False

# # # #     # 2. Find or Create the Log Sheet inside the Master Folder
# # # #     if not st.session_state.get('log_sheet_id'):
# # # #         log_sheet_id = find_or_create_log_sheet(drive_service, st.session_state.sheets_service, st.session_state.master_drive_folder_id)
# # # #         if not log_sheet_id:
# # # #             st.error("Failed to create the application log sheet. Logging will be disabled.")
# # # #         st.session_state.log_sheet_id = log_sheet_id

# # # #     # 3. Find or Create the MCM Periods Config File inside the Master Folder
# # # #     if not st.session_state.get('mcm_periods_drive_file_id'):
# # # #         mcm_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE, parent_id=st.session_state.master_drive_folder_id)
# # # #         if not mcm_file_id:
# # # #             st.info(f"MCM Periods config file '{MCM_PERIODS_FILENAME_ON_DRIVE}' not found, creating it...")
# # # #             save_mcm_periods(drive_service, {}) # Create an empty config file
# # # #         else:
# # # #             st.session_state.mcm_periods_drive_file_id = mcm_file_id

# # # #     return True
# # # def initialize_drive_structure(drive_service, sheets_service):
# # #     """
# # #     Initializes the entire application folder and file structure within the specified Shared Drive.
# # #     This is the central point of control for ensuring all files are created in the correct location.
# # #     """
# # #     if not SHARED_DRIVE_ID or "PASTE" in SHARED_DRIVE_ID:
# # #         st.error("CRITICAL: `SHARED_DRIVE_ID` is not configured in `config.py`. Please follow the setup instructions.")
# # #         return False

# # #     # 1. Find or Create the Master Folder inside the Shared Drive
# # #     master_id = st.session_state.get('master_drive_folder_id')
# # #     if not master_id:
# # #         master_id = find_drive_item_by_name(drive_service, MASTER_DRIVE_FOLDER_NAME,
# # #                                             'application/vnd.google-apps.folder', parent_id=SHARED_DRIVE_ID)
# # #         if not master_id:
# # #             st.info(f"Master folder '{MASTER_DRIVE_FOLDER_NAME}' not found in Shared Drive, creating it...")
# # #             master_id, _ = create_drive_folder(drive_service, MASTER_DRIVE_FOLDER_NAME, parent_id=SHARED_DRIVE_ID)
# # #             if not master_id:
# # #                 st.error(f"Fatal: Failed to create master folder '{MASTER_DRIVE_FOLDER_NAME}'. Cannot proceed.")
# # #                 return False
# # #         st.session_state.master_drive_folder_id = master_id

# # #     if not st.session_state.master_drive_folder_id:
# # #         st.error("Master Drive folder ID could not be established. Cannot proceed.")
# # #         return False

# # #     # 2. Find or Create the Log Sheet inside the Master Folder
# # #     if not st.session_state.get('log_sheet_id'):
# # #         log_sheet_id = find_or_create_log_sheet(drive_service, sheets_service, st.session_state.master_drive_folder_id)
# # #         if not log_sheet_id:
# # #             st.error("Failed to create the application log sheet. Logging will be disabled.")
# # #         st.session_state.log_sheet_id = log_sheet_id

# # #     # 3. Find or Create the MCM Periods Config File inside the Master Folder
# # #     if not st.session_state.get('mcm_periods_drive_file_id'):
# # #         mcm_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE, parent_id=st.session_state.master_drive_folder_id)
# # #         if not mcm_file_id:
# # #             st.info(f"MCM Periods config file '{MCM_PERIODS_FILENAME_ON_DRIVE}' not found, creating it...")
# # #             save_mcm_periods(drive_service, {}) # Create an empty config file
# # #         else:
# # #             st.session_state.mcm_periods_drive_file_id = mcm_file_id

# # #     return True

# # # def upload_to_drive(drive_service, file_content_or_path, folder_id, filename_on_drive):
# # #     """Uploads a file to a specific folder, supporting Shared Drives."""
# # #     try:
# # #         file_metadata = {'name': filename_on_drive, 'parents': [folder_id]}
# # #         media_body = None

# # #         if isinstance(file_content_or_path, bytes):
# # #             fh = BytesIO(file_content_or_path)
# # #             media_body = MediaIoBaseUpload(fh, mimetype='application/pdf', resumable=True)
# # #         else:
# # #             st.error(f"Unsupported file content type for Google Drive upload: {type(file_content_or_path)}")
# # #             return None, None

# # #         request = drive_service.files().create(
# # #             body=file_metadata,
# # #             media_body=media_body,
# # #             fields='id, webViewLink',
# # #             supportsAllDrives=True
# # #         )
# # #         file = request.execute()
# # #         return file.get('id'), file.get('webViewLink')
# # #     except HttpError as error:
# # #         st.error(f"An API error occurred uploading to Drive: {error}")
# # #         return None, None
# # #     except Exception as e:
# # #         st.error(f"An unexpected error in upload_to_drive: {e}")
# # #         return None, None

# # # def create_spreadsheet(sheets_service, drive_service, title, parent_folder_id=None):
# # #     """Creates a spreadsheet and moves it to a specific folder, supporting Shared Drives."""
# # #     try:
# # #         spreadsheet_body = {'properties': {'title': title}}
# # #         spreadsheet = sheets_service.spreadsheets().create(body=spreadsheet_body,
# # #                                                            fields='spreadsheetId,spreadsheetUrl').execute()
# # #         spreadsheet_id = spreadsheet.get('spreadsheetId')

# # #         if spreadsheet_id and drive_service and parent_folder_id:
# # #             file = drive_service.files().get(fileId=spreadsheet_id, fields='parents').execute()
# # #             previous_parents = ",".join(file.get('parents'))
# # #             drive_service.files().update(
# # #                 fileId=spreadsheet_id,
# # #                 addParents=parent_folder_id,
# # #                 removeParents=previous_parents,
# # #                 fields='id, parents',
# # #                 supportsAllDrives=True
# # #             ).execute()
# # #         return spreadsheet_id, spreadsheet.get('spreadsheetUrl')
# # #     except HttpError as error:
# # #         st.error(f"An error occurred creating Spreadsheet: {error}")
# # #         return None, None
# # #     except Exception as e:
# # #         st.error(f"An unexpected error occurred creating Spreadsheet: {e}")
# # #         return None, None

# # # def find_or_create_log_sheet(drive_service, sheets_service, parent_folder_id):
# # #     """Finds the log sheet or creates it if it doesn't exist."""
# # #     log_sheet_name = LOG_SHEET_FILENAME_ON_DRIVE
# # #     log_sheet_id = find_drive_item_by_name(drive_service, log_sheet_name,
# # #                                            mime_type='application/vnd.google-apps.spreadsheet',
# # #                                            parent_id=parent_folder_id)
# # #     if log_sheet_id:
# # #         return log_sheet_id
    
# # #     st.info(f"Log sheet '{log_sheet_name}' not found. Creating it...")
# # #     spreadsheet_id, _ = create_spreadsheet(sheets_service, drive_service, log_sheet_name, parent_folder_id=parent_folder_id)
    
# # #     if spreadsheet_id:
# # #         header = [['Timestamp', 'Username', 'Role']]
# # #         body = {'values': header}
# # #         try:
# # #             sheets_service.spreadsheets().values().append(
# # #                 spreadsheetId=spreadsheet_id, range='Sheet1!A1',
# # #                 valueInputOption='USER_ENTERED', body=body
# # #             ).execute()
# # #             st.success(f"Log sheet '{log_sheet_name}' created successfully.")
# # #         except HttpError as error:
# # #             st.error(f"Failed to write header to new log sheet: {error}")
# # #             return None
# # #         return spreadsheet_id
# # #     else:
# # #         st.error(f"Fatal: Failed to create log sheet '{log_sheet_name}'. Logging will be disabled.")
# # #         return None

# # # def log_activity(sheets_service, log_sheet_id, username, role):
# # #     """Appends a login activity record to the specified log sheet."""
# # #     if not log_sheet_id:
# # #         st.warning("Log Sheet ID is not available. Skipping activity logging.")
# # #         return False
    
# # #     try:
# # #         timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
# # #         values = [[timestamp, username, role]]
# # #         body = {'values': values}
        
# # #         sheets_service.spreadsheets().values().append(
# # #             spreadsheetId=log_sheet_id,
# # #             range='Sheet1!A1',
# # #             valueInputOption='USER_ENTERED',
# # #             insertDataOption='INSERT_ROWS',
# # #             body=body
# # #         ).execute()
# # #         return True
# # #     except HttpError as error:
# # #         st.error(f"An error occurred while logging activity: {error}")
# # #         return False
# # #     except Exception as e:
# # #         st.error(f"An unexpected error occurred during logging: {e}")
# # #         return False

# # # def find_or_create_spreadsheet(drive_service, sheets_service, sheet_name, parent_folder_id):
# # #     """Finds a spreadsheet by name or creates it with a header if it doesn't exist."""
# # #     sheet_id = find_drive_item_by_name(drive_service, sheet_name,
# # #                                        mime_type='application/vnd.google-apps.spreadsheet',
# # #                                        parent_id=parent_folder_id)
# # #     if sheet_id:
# # #         return sheet_id

# # #     st.info(f"Spreadsheet '{sheet_name}' not found. Creating it...")
# # #     sheet_id, _ = create_spreadsheet(sheets_service, drive_service, sheet_name, parent_folder_id=parent_folder_id)
    
# # #     if sheet_id:
# # #         header = []
# # #         if sheet_name == SMART_AUDIT_MASTER_DB_SHEET_NAME:
# # #             header = [[
# # #                 "GSTIN", "Trade Name", "Category", "Allocated Audit Group Number", 
# # #                 "Allocated Circle", "Financial Year", "Allocated Date", "Uploaded Date", 
# # #                 "Office Order PDF Path", "Reassigned Flag", "Old Group Number", "Old Circle Number"
# # #             ]]
# # #         elif sheet_name == LOG_SHEET_FILENAME_ON_DRIVE:
# # #              header = [['Timestamp', 'Username', 'Role']]
        
# # #         if header:
# # #             body = {'values': header}
# # #             try:
# # #                 sheets_service.spreadsheets().values().append(
# # #                     spreadsheetId=sheet_id, range='Sheet1!A1',
# # #                     valueInputOption='USER_ENTERED', body=body
# # #                 ).execute()
# # #                 st.success(f"Spreadsheet '{sheet_name}' created successfully with headers.")
# # #             except HttpError as error:
# # #                 st.error(f"Failed to write header to new spreadsheet: {error}")
# # #                 return None
# # #         return sheet_id
# # #     else:
# # #         st.error(f"Fatal: Failed to create spreadsheet '{sheet_name}'.")
# # #         return None

# # # def read_from_spreadsheet(sheets_service, spreadsheet_id, sheet_name="Sheet1"):
# # #     """Reads an entire sheet into a pandas DataFrame, handling varying column counts."""
# # #     try:
# # #         result = sheets_service.spreadsheets().values().get(
# # #             spreadsheetId=spreadsheet_id,
# # #             range=sheet_name
# # #         ).execute()
# # #         values = result.get('values', [])

# # #         if not values:
# # #             return pd.DataFrame()

# # #         header = values[0]
# # #         data = values[1:]
        
# # #         if not data:
# # #             return pd.DataFrame(columns=header)

# # #         num_cols = len(header)
# # #         processed_data = []
# # #         for row in data:
# # #             new_row = list(row)
# # #             if len(new_row) < num_cols:
# # #                 new_row.extend([None] * (num_cols - len(new_row)))
# # #             elif len(new_row) > num_cols:
# # #                 new_row = new_row[:num_cols]
# # #             processed_data.append(new_row)

# # #         df = pd.DataFrame(processed_data, columns=header)
# # #         return df

# # #     except HttpError as error:
# # #         st.error(f"An API error occurred reading from Spreadsheet: {error}")
# # #         return pd.DataFrame()
# # #     except Exception as e:
# # #         st.error(f"An unexpected error occurred while reading the Spreadsheet: {e}")
# # #         return pd.DataFrame()

# # # def update_spreadsheet_from_df(sheets_service, spreadsheet_id, df_to_write):
# # #     """Clears a sheet and updates it with data from a pandas DataFrame."""
# # #     try:
# # #         sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
# # #         first_sheet_title = sheet_metadata['sheets'][0]['properties']['title']

# # #         sheets_service.spreadsheets().values().clear(
# # #             spreadsheetId=spreadsheet_id,
# # #             range=first_sheet_title
# # #         ).execute()

# # #         df_prepared = df_to_write.fillna('')
# # #         values_to_write = [df_prepared.columns.values.tolist()] + df_prepared.values.tolist()

# # #         body = {'values': values_to_write}
# # #         sheets_service.spreadsheets().values().update(
# # #             spreadsheetId=spreadsheet_id,
# # #             range=f"{first_sheet_title}!A1",
# # #             valueInputOption='USER_ENTERED',
# # #             body=body
# # #         ).execute()
        
# # #         return True

# # #     except HttpError as error:
# # #         st.error(f"An API error occurred while updating the Spreadsheet: {error}")
# # #         return False
# # #     except Exception as e:
# # #         st.error(f"An unexpected error occurred while updating the Spreadsheet: {e}")
# # #         return False

# # # def load_mcm_periods(drive_service):
# # #     """Loads the MCM periods configuration file from Google Drive."""
# # #     mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
# # #     if not mcm_periods_file_id:
# # #         if st.session_state.get('master_drive_folder_id'):
# # #             mcm_periods_file_id = find_drive_item_by_name(drive_service, MCM_PERIODS_FILENAME_ON_DRIVE,
# # #                                                           parent_id=st.session_state.master_drive_folder_id)
# # #             st.session_state.mcm_periods_drive_file_id = mcm_periods_file_id
# # #         else:
# # #             return {}

# # #     if mcm_periods_file_id:
# # #         try:
# # #             request = drive_service.files().get_media(fileId=mcm_periods_file_id)
# # #             fh = BytesIO()
# # #             downloader = MediaIoBaseDownload(fh, request)
# # #             done = False
# # #             while not done:
# # #                 status, done = downloader.next_chunk()
# # #             fh.seek(0)
# # #             return json.load(fh)
# # #         except HttpError as error:
# # #             if error.resp.status == 404:
# # #                 st.session_state.mcm_periods_drive_file_id = None
# # #             else:
# # #                 st.error(f"Error loading '{MCM_PERIODS_FILENAME_ON_DRIVE}' from Drive: {error}")
# # #             return {}
# # #         except json.JSONDecodeError:
# # #             st.error(f"Error decoding JSON from '{MCM_PERIODS_FILENAME_ON_DRIVE}'. File might be corrupted.")
# # #             return {}
# # #         except Exception as e:
# # #             st.error(f"Unexpected error loading '{MCM_PERIODS_FILENAME_ON_DRIVE}': {e}")
# # #             return {}
# # #     return {}

# # # def save_mcm_periods(drive_service, periods_data):
# # #     """Saves the MCM periods configuration file to Google Drive."""
# # #     master_folder_id = st.session_state.get('master_drive_folder_id')
# # #     if not master_folder_id:
# # #         st.error("Master Drive folder ID not set. Cannot save MCM periods configuration.")
# # #         return False

# # #     mcm_periods_file_id = st.session_state.get('mcm_periods_drive_file_id')
# # #     file_content = json.dumps(periods_data, indent=4).encode('utf-8')
# # #     fh = BytesIO(file_content)
# # #     media_body = MediaIoBaseUpload(fh, mimetype='application/json', resumable=True)

# # #     try:
# # #         if mcm_periods_file_id:
# # #             file_metadata_update = {'name': MCM_PERIODS_FILENAME_ON_DRIVE}
# # #             drive_service.files().update(
# # #                 fileId=mcm_periods_file_id,
# # #                 body=file_metadata_update,
# # #                 media_body=media_body,
# # #                 fields='id, name',
# # #                 supportsAllDrives=True
# # #             ).execute()
# # #         else:
# # #             file_metadata_create = {'name': MCM_PERIODS_FILENAME_ON_DRIVE, 'parents': [master_folder_id]}
# # #             new_file = drive_service.files().create(
# # #                 body=file_metadata_create,
# # #                 media_body=media_body,
# # #                 fields='id, name',
# # #                 supportsAllDrives=True
# # #             ).execute()
# # #             st.session_state.mcm_periods_drive_file_id = new_file.get('id')
# # #         return True
# # #     except HttpError as error:
# # #         st.error(f"Error saving '{MCM_PERIODS_FILENAME_ON_DRIVE}' to Drive: {error}")
# # #         return False
# # #     except Exception as e:
# # #         st.error(f"Unexpected error saving '{MCM_PERIODS_FILENAME_ON_DRIVE}': {e}")
# # #         return False

# # # def append_to_spreadsheet(sheets_service, spreadsheet_id, values_to_append):
# # #     """Appends rows to a spreadsheet."""
# # #     try:
# # #         body = {'values': values_to_append}
# # #         sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
# # #         sheets = sheet_metadata.get('sheets', '')
# # #         first_sheet_title = sheets[0].get("properties", {}).get("title", "Sheet1")

# # #         append_result = sheets_service.spreadsheets().values().append(
# # #             spreadsheetId=spreadsheet_id,
# # #             range=f"{first_sheet_title}!A1",
# # #             valueInputOption='USER_ENTERED',
# # #             insertDataOption='INSERT_ROWS',
# # #             body=body
# # #         ).execute()
# # #         return append_result
# # #     except HttpError as error:
# # #         st.error(f"An error occurred appending to Spreadsheet: {error}")
# # #         return None
# # #     except Exception as e:
# # #         st.error(f"Unexpected error appending to Spreadsheet: {e}")
# # #         return None
# # # def delete_spreadsheet_rows(sheets_service, spreadsheet_id, sheet_id_gid, row_indices_to_delete):
# # #     """Deletes specific rows from a sheet."""
# # #     if not row_indices_to_delete:
# # #         return True
# # #     requests = []
# # #     # Sort in descending order to avoid index shifting issues during deletion
# # #     for data_row_index in sorted(row_indices_to_delete, reverse=True):
# # #         # The API uses 0-based index. If data starts at row 2 (index 1) after the header,
# # #         # the sheet row index for the API is data_row_index + 1.
# # #         sheet_row_start_index = data_row_index + 1
# # #         requests.append({
# # #             "deleteDimension": {
# # #                 "range": {
# # #                     "sheetId": sheet_id_gid,
# # #                     "dimension": "ROWS",
# # #                     "startIndex": sheet_row_start_index,
# # #                     "endIndex": sheet_row_start_index + 1
# # #                 }
# # #             }
# # #         })
# # #     if requests:
# # #         try:
# # #             body = {'requests': requests}
# # #             sheets_service.spreadsheets().batchUpdate(
# # #                 spreadsheetId=spreadsheet_id, body=body).execute()
# # #             return True
# # #         except HttpError as error:
# # #             st.error(f"An error occurred deleting rows from Spreadsheet: {error}")
# # #             return False
# # #         except Exception as e:
# # #             st.error(f"Unexpected error deleting rows: {e}")
# # #             return False
# # #     return True


