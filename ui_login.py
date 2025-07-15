# ui_login.py
import streamlit as st
import os
import base64
from config import USER_CREDENTIALS, USER_ROLES, AUDIT_GROUP_NUMBERS

def login_page():
    """
    Renders the login page for the application.
    This version is simplified for a service account backend and does not
    contain any user-facing Google OAuth logic or circular imports.
    """
    # Define the CSS style
    st.markdown("""
    <style>
    .page-main-title {
        font-size: 3rem;
        font-weight: bold;
        text-align: center;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Render the title
    st.markdown("<div class='page-main-title'>e-MCM App</div>", unsafe_allow_html=True)
    st.markdown("<h2 class='page-app-subtitle'>GST Audit 1 Commissionerate</h2>", unsafe_allow_html=True)

    def get_image_base64_str(img_path):
        try:
            with open(img_path, "rb") as img_file:
                return base64.b64encode(img_file.read()).decode('utf-8')
        except FileNotFoundError:
            # Silently fail if logo is not found
            return None
        except Exception as e:
            st.error(f"Error reading image file {img_path}: {e}")
            return None

    # Display the logo if it exists
    base64_image = get_image_base64_str("logo.png")
    if base64_image:
        st.markdown(
            f"<div class='login-header'><img src='data:image/png;base64,{base64_image}' alt='Logo' class='login-logo'></div>",
            unsafe_allow_html=True)

    st.markdown("""
    <div class='app-description'>
        Welcome! This digital platform streamlines Draft Audit Report (DAR) collection, processing and compilation from Audit Groups for MCM 
         purpose using  AI-powered data extraction.
    </div>
    """, unsafe_allow_html=True)

    # --- Internal Application Login Form ---
    username = st.text_input("Username", key="login_username_styled", placeholder="Enter your username")
    password = st.text_input("Password", type="password", key="login_password_styled",
                             placeholder="Enter your password")

    if st.button("Login", key="login_button_styled", use_container_width=True):
        if username in USER_CREDENTIALS and USER_CREDENTIALS[username] == password:
            st.session_state.logged_in = True
            st.session_state.username = username
            st.session_state.role = USER_ROLES[username]
            if st.session_state.role == "AuditGroup":
                st.session_state.audit_group_no = AUDIT_GROUP_NUMBERS[username]
            
            st.success(f"Logged in as {username} ({st.session_state.role})")
            
            # Reset flags to trigger re-initialization of services and structure
            st.session_state.drive_structure_initialized = False
            st.session_state.login_event_logged = False
            st.rerun()
        else:
            st.error("Invalid username or password")
