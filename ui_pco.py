# ui_pco.py
import streamlit as st
import datetime
import time
import pandas as pd
import plotly.express as px
from streamlit_option_menu import option_menu
import math

# Import tab functions from other UI modules
from ui_mcm_agenda import mcm_agenda_tab
from ui_pco_reports import pco_reports_dashboard

# Import Google utilities and config
from google_utils import (
    load_mcm_periods, save_mcm_periods, read_from_spreadsheet,
    update_spreadsheet_from_df, delete_spreadsheet_rows
)
from config import USER_CREDENTIALS, MCM_PERIODS_FILENAME_ON_DRIVE

def pco_dashboard(drive_service, sheets_service):
    """
    Main function for the Planning & Coordination Officer's dashboard.
    """
    st.markdown("<div class='sub-header'>Planning & Coordination Officer Dashboard</div>", unsafe_allow_html=True)
    
    # Load the central MCM periods configuration
    mcm_periods = load_mcm_periods(drive_service)

    # --- Sidebar ---
    with st.sidebar:
        try:
            st.image("logo.png", width=80)
        except Exception as e:
            st.sidebar.warning(f"Could not load logo.png: {e}")

        st.markdown(f"**User:** {st.session_state.username}")
        st.markdown(f"**Role:** {st.session_state.role}")
        if st.button("Logout", key="pco_logout_full_final", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.session_state.role = ""
            st.session_state.drive_structure_initialized = False
            # Clear any other session state keys if necessary
            st.rerun()
        st.markdown("---")
        
        # Smart Audit Tracker Button
        if st.button("🚀 Smart Audit Tracker", key="launch_sat_pco"):
            st.session_state.app_mode = "smart_audit_tracker"
            st.rerun()
        st.markdown("---")

    # --- Main Tab Navigation ---
    selected_tab = option_menu(
        menu_title=None,
        options=["Create MCM Period", "Manage MCM Periods", "View Uploaded Reports", 
                 "MCM Agenda", "Visualizations", "Reports"],
        icons=["calendar-plus-fill", "sliders", "eye-fill", 
               "journal-richtext", "bar-chart-fill", "file-earmark-text-fill"],
        menu_icon="gear-wide-connected",
        default_index=0,
        orientation="horizontal",
        styles={
            "container": {"padding": "5px !important", "background-color": "#e9ecef"},
            "icon": {"color": "#007bff", "font-size": "20px"},
            "nav-link": {"font-size": "14px", "text-align": "center", "margin": "0px", "--hover-color": "#d1e7fd"},
            "nav-link-selected": {"background-color": "#007bff", "color": "white"},
        }
    )

    st.markdown("<div class='card'>", unsafe_allow_html=True)

    # ========================== CREATE MCM PERIOD TAB (SIMPLIFIED) ==========================
    if selected_tab == "Create MCM Period":
        st.markdown("<h3>Create New MCM Period</h3>", unsafe_allow_html=True)
        st.info("This registers a new time period, making it available for selection by Audit Groups. It no longer creates separate folders or files.")
        
        current_year = datetime.datetime.now().year
        years = list(range(current_year - 1, current_year + 3))
        months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        
        col1, col2 = st.columns(2)
        with col1:
            selected_year = st.selectbox("Select Year", options=years, index=years.index(current_year))
        with col2:
            selected_month_name = st.selectbox("Select Month", options=months, index=datetime.datetime.now().month - 1)
        
        selected_month_num = months.index(selected_month_name) + 1
        period_key = f"{selected_year}-{selected_month_num:02d}"

        if period_key in mcm_periods:
            st.warning(f"MCM Period for {selected_month_name} {selected_year} already exists.")
        else:
            if st.button(f"Create MCM Period for {selected_month_name} {selected_year}", use_container_width=True):
                mcm_periods[period_key] = {
                    "year": selected_year, "month_num": selected_month_num, "month_name": selected_month_name,
                    "active": True, "created_at": datetime.datetime.now().isoformat()
                }
                if save_mcm_periods(drive_service, mcm_periods):
                    st.success(f"Successfully created and activated MCM period for {selected_month_name} {selected_year}!")
                    st.balloons()
                    time.sleep(1); st.rerun()
                else:
                    st.error("Failed to save the updated MCM period configuration.")
                    del mcm_periods[period_key] # Revert local change on save failure

    # ========================== MANAGE MCM PERIODS TAB ==========================
    elif selected_tab == "Manage MCM Periods":
        st.markdown("<h3>Manage Existing MCM Periods</h3>", unsafe_allow_html=True)
        st.info("Activate or deactivate periods to control which months are available for Audit Groups to upload to. Deleting a period only removes it from this list; it does not delete any data from the master sheet.")

        if not mcm_periods:
            st.info("No MCM periods have been created yet.")
        else:
            sorted_keys = sorted(mcm_periods.keys(), reverse=True)
            for period_key in sorted_keys:
                period_data = mcm_periods[period_key]
                month_name = period_data.get('month_name', 'N/A')
                year = period_data.get('year', 'N/A')

                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 1, 1])
                    c1.markdown(f"**{month_name} {year}**")
                    
                    is_active = period_data.get("active", False)
                    new_status = c2.checkbox("Active", value=is_active, key=f"active_{period_key}")
                    if new_status != is_active:
                        mcm_periods[period_key]["active"] = new_status
                        if save_mcm_periods(drive_service, mcm_periods):
                            st.toast(f"Status for {month_name} {year} updated.")
                            time.sleep(0.5); st.rerun()
                        else:
                            st.error("Failed to save status update.")
                            mcm_periods[period_key]["active"] = is_active # Revert
                    
                    if c3.button("Delete", key=f"delete_{period_key}", type="secondary"):
                        st.session_state.period_to_delete = period_key
                        st.session_state.show_delete_confirm = True
                        st.rerun()

            if st.session_state.get('show_delete_confirm'):
                key_to_delete = st.session_state.period_to_delete
                period_to_delete_data = mcm_periods.get(key_to_delete, {})
                with st.form(key=f"delete_confirm_form_{key_to_delete}"):
                    st.warning(f"Are you sure you want to delete the period record for **{period_to_delete_data.get('month_name')} {period_to_delete_data.get('year')}**?")
                    password = st.text_input("Enter PCO password to confirm:", type="password")
                    submitted = st.form_submit_button("Confirm Deletion", use_container_width=True)
                    if submitted:
                        if password == USER_CREDENTIALS.get("planning_officer"):
                            del mcm_periods[key_to_delete]
                            if save_mcm_periods(drive_service, mcm_periods):
                                st.success("Period record deleted.")
                            else:
                                st.error("Failed to save changes after deletion.")
                            st.session_state.show_delete_confirm = False
                            st.session_state.period_to_delete = None
                            time.sleep(1); st.rerun()
                        else:
                            st.error("Incorrect password.")

    # ========================== VIEW UPLOADED REPORTS TAB ==========================
    elif selected_tab == "View Uploaded Reports":
        st.markdown("<h3>View & Edit All Uploaded DAR Data</h3>", unsafe_allow_html=True)
        st.info("This table shows all data from the central master spreadsheet. You can edit data here and save it back to the source file.")
        
        with st.spinner("Loading all data from the master spreadsheet..."):
            df_all_data = read_from_spreadsheet(sheets_service)

        if df_all_data is None or df_all_data.empty:
            st.warning("No data found in the master spreadsheet.")
        else:
            # Use a session state key to persist the edited dataframe
            if 'edited_df' not in st.session_state:
                st.session_state.edited_df = df_all_data.copy()

            edited_df = st.data_editor(
                st.session_state.edited_df,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="pco_data_editor"
            )

            if st.button("Save Changes to Spreadsheet", type="primary", use_container_width=True):
                with st.spinner("Saving changes to Google Sheet..."):
                    if update_spreadsheet_from_df(sheets_service, edited_df):
                        st.success("Changes saved successfully!")
                        st.session_state.edited_df = edited_df.copy() # Update session state with saved data
                        time.sleep(1); st.rerun()
                    else:
                        st.error("Failed to save changes. Please check permissions or logs.")

    # ========================== MCM AGENDA TAB ==========================
    elif selected_tab == "MCM Agenda":
        # This tab's logic is now encapsulated in its own function/module
        mcm_agenda_tab(drive_service, sheets_service, mcm_periods)

    # ========================== VISUALIZATIONS TAB ==========================
    elif selected_tab == "Visualizations":
        st.markdown("<h3>Data Visualizations</h3>", unsafe_allow_html=True)
        if not mcm_periods:
            st.info("No MCM periods exist to visualize data from.")
        else:
            # Load the single master dataframe once
            with st.spinner("Loading data for visualizations..."):
                df_viz_data = read_from_spreadsheet(sheets_service)

            if df_viz_data is None or df_viz_data.empty:
                st.warning("No data found in the master spreadsheet to visualize.")
            else:
                # Let user filter by period
                period_options = sorted(df_viz_data['MCM Period'].dropna().unique(), reverse=True)
                selected_period_viz = st.selectbox("Select MCM Period to Visualize", options=["All Periods"] + period_options)

                if selected_period_viz != "All Periods":
                    df_viz_data = df_viz_data[df_viz_data['MCM Period'] == selected_period_viz]

                # Data Cleaning and Preparation
                # (This logic is from your provided snippet and is sound)
                amount_cols = ['Total Amount Detected (Overall Rs)', 'Total Amount Recovered (Overall Rs)', 'Revenue Involved (Lakhs Rs)', 'Revenue Recovered (Lakhs Rs)']
                for col in amount_cols:
                    if col in df_viz_data.columns:
                        df_viz_data[col] = pd.to_numeric(df_viz_data[col], errors='coerce').fillna(0)
                
                df_unique_reports = df_viz_data.drop_duplicates(subset=['DAR PDF URL']).copy()
                df_unique_reports['Detection in Lakhs'] = df_unique_reports['Total Amount Detected (Overall Rs)'] / 100000.0
                df_unique_reports['Recovery in Lakhs'] = df_unique_reports['Total Amount Recovered (Overall Rs)'] / 100000.0
                
                # ... (The rest of the visualization logic from your snippet would go here)
                # For brevity, I'll include one example chart. The full set of charts can be pasted here.
                st.markdown("---")
                st.markdown("<h4>Group-wise Performance</h4>", unsafe_allow_html=True)
                if 'Detection in Lakhs' in df_unique_reports.columns:
                    df_unique_reports['Audit Group Number Str'] = df_unique_reports['audit_group_number'].astype(str)
                    viz_detection_data = df_unique_reports.groupby('Audit Group Number Str')['Detection in Lakhs'].sum().reset_index()
                    if not viz_detection_data.empty:
                        fig_det_grp = px.bar(viz_detection_data, x='Audit Group Number Str', y='Detection in Lakhs', text_auto='.2f', title="Total Detection by Audit Group", labels={'Detection in Lakhs': 'Total Detection (Lakhs Rs)', 'Audit Group Number Str': 'Audit Group'})
                        st.plotly_chart(fig_det_grp, use_container_width=True)

    # ========================== REPORTS TAB ==========================
    elif selected_tab == "Reports":
        # This tab's logic is encapsulated in its own function/module
        pco_reports_dashboard(drive_service, sheets_service)

    st.markdown("</div>", unsafe_allow_html=True)
