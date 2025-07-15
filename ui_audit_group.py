# ui_audit_group.py
import streamlit as st
import pandas as pd
import datetime
import math
from io import BytesIO
import time

# Use the new centralized google_utils functions
from google_utils import (
    load_mcm_periods, upload_to_drive, append_to_spreadsheet,
    read_from_spreadsheet, delete_spreadsheet_rows
)
from dar_processor import preprocess_pdf_text
from gemini_utils import get_structured_data_with_gemini
from validation_utils import validate_data_for_sheet, VALID_CATEGORIES, VALID_PARA_STATUSES
from config import USER_CREDENTIALS, AUDIT_GROUP_NUMBERS
from models import ParsedDARReport

from streamlit_option_menu import option_menu

# This is the order of columns for the master spreadsheet
# It now includes 'MCM Period' as the first column for easy filtering.
SHEET_DATA_COLUMNS_ORDER = [
    "MCM Period", "audit_group_number", "audit_circle_number", "gstin", "trade_name", "category",
    "total_amount_detected_overall_rs", "total_amount_recovered_overall_rs",
    "audit_para_number", "audit_para_heading",
    "revenue_involved_lakhs_rs", "revenue_recovered_lakhs_rs", "status_of_para",
]

# This is the order for the st.data_editor in the UI
DISPLAY_COLUMN_ORDER_EDITOR = [
    "audit_group_number", "audit_circle_number", "gstin", "trade_name", "category",
    "total_amount_detected_overall_rs", "total_amount_recovered_overall_rs",
    "audit_para_number", "audit_para_heading",
    "revenue_involved_lakhs_rs", "revenue_recovered_lakhs_rs", "status_of_para"
]

def calculate_audit_circle(audit_group_number_val):
    try:
        agn = int(audit_group_number_val)
        return math.ceil(agn / 3.0) if 1 <= agn <= 30 else None
    except (ValueError, TypeError):
        return None

def audit_group_dashboard(drive_service, sheets_service):
    st.markdown(f"<div class='sub-header'>Audit Group {st.session_state.audit_group_no} Dashboard</div>", unsafe_allow_html=True)
    
    mcm_periods_all = load_mcm_periods(drive_service)
    active_periods = {k: v for k, v in mcm_periods_all.items() if v.get("active")}
    
    # Rest of the dashboard setup...
    # (Sidebar, logout, etc. remains the same)
    with st.sidebar:
        st.markdown(f"**User:** {st.session_state.username}<br>**Group No:** {st.session_state.audit_group_no}", unsafe_allow_html=True)
        if st.button("Logout", key="ag_logout_main", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.session_state.role = ""
            st.rerun()

    selected_tab = option_menu(
        menu_title="Audit Group Menu",
        options=["Upload DAR for MCM", "View My Uploaded DARs", "Delete My DAR Entries"],
        icons=["cloud-upload-fill", "eye-fill", "trash2-fill"],
        default_index=0, orientation="horizontal"
    )

    st.markdown("<div class='card'>", unsafe_allow_html=True)

    # ========================== UPLOAD DAR FOR MCM TAB (ADAPTED) ==========================
    if selected_tab == "Upload DAR for MCM":
        st.markdown("<h3>Upload DAR PDF for MCM Period</h3>", unsafe_allow_html=True)
        if not active_periods:
            st.warning("No active MCM periods are available for upload. Please contact the Planning Officer.")
        else:
            # Create a display map for the selectbox
            period_options_map = {k: f"{v.get('month_name')} {v.get('year')}" for k, v in sorted(active_periods.items(), key=lambda x: x[0], reverse=True)}
            
            selected_period_key = st.selectbox(
                "Select Active MCM Period",
                options=list(period_options_map.keys()),
                format_func=lambda k: period_options_map[k]
            )

            if selected_period_key:
                selected_period_display_name = period_options_map[selected_period_key]
                st.info(f"You are uploading a DAR for the **{selected_period_display_name}** period.")
                
                uploaded_file = st.file_uploader("Choose DAR PDF", type="pdf", key=f"uploader_{selected_period_key}")

                if uploaded_file:
                    if st.button("Extract & Prepare Data", use_container_width=True):
                        with st.spinner(f"Processing '{uploaded_file.name}'..."):
                            pdf_bytes = uploaded_file.getvalue()
                            
                            # The upload function no longer needs a folder_id, it uses the central one
                            dar_filename = f"AG{st.session_state.audit_group_no}_{selected_period_key}_{uploaded_file.name}"
                            pdf_drive_id, pdf_drive_url = upload_to_drive(drive_service, pdf_bytes, dar_filename)

                            if not pdf_drive_id:
                                st.error("Failed to upload PDF to Google Drive. Cannot proceed.")
                                st.stop()
                            
                            st.session_state.pdf_drive_url_for_submission = pdf_drive_url
                            st.success(f"DAR PDF uploaded successfully: [View Link]({pdf_drive_url})")

                            # AI processing remains the same
                            preprocessed_text = preprocess_pdf_text(BytesIO(pdf_bytes))
                            parsed_data = get_structured_data_with_gemini(st.secrets["GEMINI_API_KEY"], preprocessed_text)
                            
                            # Flatten data for the editor
                            temp_list = []
                            header_info = parsed_data.header.model_dump() if parsed_data.header else {}
                            base_info = {
                                "audit_group_number": st.session_state.audit_group_no,
                                "audit_circle_number": calculate_audit_circle(st.session_state.audit_group_no),
                                **{k: v for k, v in header_info.items() if k in DISPLAY_COLUMN_ORDER_EDITOR}
                            }

                            if parsed_data.audit_paras:
                                for para in parsed_data.audit_paras:
                                    row = base_info.copy()
                                    row.update(para.model_dump())
                                    temp_list.append(row)
                            else:
                                st.warning("No audit paras were automatically extracted. A template row has been created. Please fill it manually.")
                                temp_list.append(base_info)
                            
                            df_extracted = pd.DataFrame(temp_list)
                            st.session_state.editor_data = df_extracted[DISPLAY_COLUMN_ORDER_EDITOR]
                            st.rerun()

                # --- Data Editor and Submission ---
                if 'editor_data' in st.session_state and not st.session_state.editor_data.empty:
                    st.markdown("<h4>Review and Edit Extracted Data:</h4>", unsafe_allow_html=True)
                    
                    edited_df = st.data_editor(
                        st.session_state.editor_data,
                        num_rows="dynamic",
                        use_container_width=True,
                        hide_index=True
                    )

                    if st.button("Validate and Submit Data", use_container_width=True, type="primary"):
                        df_to_submit = edited_df.copy()
                        
                        # Add the crucial MCM Period column for the master sheet
                        df_to_submit.insert(0, "MCM Period", selected_period_display_name)
                        
                        # Re-calculate these to ensure they are correct
                        df_to_submit["audit_group_number"] = st.session_state.audit_group_no
                        df_to_submit["audit_circle_number"] = calculate_audit_circle(st.session_state.audit_group_no)

                        validation_errors = validate_data_for_sheet(df_to_submit)
                        if validation_errors:
                            st.error("Validation Failed!")
                            for error in validation_errors:
                                st.warning(f"- {error}")
                        else:
                            with st.spinner("Submitting to Master Google Sheet..."):
                                # Add the PDF URL and timestamp to each row
                                df_to_submit["DAR PDF URL"] = st.session_state.get('pdf_drive_url_for_submission', 'URL Missing')
                                df_to_submit["Record Created Date"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                                # Ensure all columns for the sheet are present
                                final_columns = SHEET_DATA_COLUMNS_ORDER + ["DAR PDF URL", "Record Created Date"]
                                for col in final_columns:
                                    if col not in df_to_submit.columns:
                                        df_to_submit[col] = None
                                
                                rows_for_sheet = df_to_submit[final_columns].values.tolist()
                                
                                # The append function no longer needs a sheet_id
                                if append_to_spreadsheet(sheets_service, rows_for_sheet):
                                    st.success("Data submitted successfully!")
                                    st.balloons()
                                    # Clear session state for next upload
                                    del st.session_state.editor_data
                                    del st.session_state.pdf_drive_url_for_submission
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error("Failed to append data to the Master Google Sheet.")

    # ========================== VIEW/DELETE TABS (ADAPTED) ==========================
    elif selected_tab in ["View My Uploaded DARs", "Delete My DAR Entries"]:
        st.markdown(f"<h3>{selected_tab}</h3>", unsafe_allow_html=True)
        
        with st.spinner("Loading all your data from the master sheet..."):
            df_all_data = read_from_spreadsheet(sheets_service)

        if df_all_data is None or df_all_data.empty:
            st.warning("No data found in the master spreadsheet.")
        else:
            # Filter for the current audit group first
            df_all_data['audit_group_number'] = pd.to_numeric(df_all_data['audit_group_number'], errors='coerce')
            my_uploads = df_all_data[df_all_data['audit_group_number'] == st.session_state.audit_group_no].copy()

            if my_uploads.empty:
                st.info("You have not uploaded any DAR data yet.")
            else:
                # Let user filter by MCM Period
                period_options = sorted(my_uploads['MCM Period'].dropna().unique(), reverse=True)
                selected_period_to_view = st.selectbox("Select MCM Period to view/delete", options=period_options)

                if selected_period_to_view:
                    df_to_display = my_uploads[my_uploads['MCM Period'] == selected_period_to_view].copy()
                    df_to_display['original_index'] = df_to_display.index

                    if selected_tab == "View My Uploaded DARs":
                        st.dataframe(df_to_display.drop(columns=['original_index']), use_container_width=True)
                    
                    elif selected_tab == "Delete My DAR Entries":
                        st.warning("⚠️ This action is irreversible and deletes all rows associated with a specific DAR upload from the master sheet.")
                        
                        # Group by the unique PDF URL to represent a single upload
                        unique_uploads = df_to_display.drop_duplicates(subset=['DAR PDF URL'])
                        
                        delete_options = {
                            f"TN: {row['trade_name']}, Uploaded: {row['Record Created Date']}": row['original_index']
                            for _, row in unique_uploads.iterrows()
                        }
                        
                        if not delete_options:
                            st.info("No uploads to delete for this period.")
                        else:
                            selected_upload_str = st.selectbox("Select an upload to delete:", options=list(delete_options.keys()))
                            
                            if st.button("Delete Selected Upload", type="primary"):
                                original_start_index = delete_options[selected_upload_str]
                                pdf_url_to_delete = df_all_data.loc[original_start_index, 'DAR PDF URL']
                                
                                # Find all rows in the original dataframe that match this PDF URL
                                indices_to_delete = df_all_data[df_all_data['DAR PDF URL'] == pdf_url_to_delete].index.tolist()
                                
                                with st.spinner(f"Deleting {len(indices_to_delete)} rows..."):
                                    if delete_spreadsheet_rows(sheets_service, indices_to_delete):
                                        st.success("Entries deleted successfully.")
                                        time.sleep(1)
                                        st.rerun()
                                    else:
                                        st.error("Failed to delete entries from the sheet.")

    st.markdown("</div>", unsafe_allow_html=True)
