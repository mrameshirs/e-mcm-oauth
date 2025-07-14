# ui_login.py - Updated with Google OAuth integration
import streamlit as st
import os
import base64
from config import USER_CREDENTIALS, USER_ROLES, AUDIT_GROUP_NUMBERS
from google_utils import get_user_info, logout_google_account

def login_page():
    # Define the CSS style
    st.markdown("""
    <style>
    .page-main-title {
        font-size: 3rem;
        font-weight: bold;
        text-align: center;
    }
    .google-user-info {
        background-color: #e8f5e9;
        padding: 15px;
        border-radius: 10px;
        border-left: 5px solid #4caf50;
        margin: 20px 0;
    }
    .oauth-info {
        background-color: #e3f2fd;
        padding: 15px;
        border-radius: 10px;
        border-left: 5px solid #2196f3;
        margin: 20px 0;
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
            st.error(f"Logo image not found at path: {img_path}. Ensure 'logo.png' is present.")
            return None
        except Exception as e:
            st.error(f"Error reading image file {img_path}: {e}")
            return None

    image_path = "logo.png"
    base64_image = get_image_base64_str(image_path)
    if base64_image:
        image_type = os.path.splitext(image_path)[1].lower().replace(".", "") or "png"
        st.markdown(
            f"<div class='login-header'><img src='data:image/{image_type};base64,{base64_image}' alt='Logo' class='login-logo'></div>",
            unsafe_allow_html=True)
    else:
        st.markdown("<div class='login-header' style='color: red; font-weight: bold;'>[Logo Not Found]</div>",
                    unsafe_allow_html=True)

    st.markdown("""
    <div class='app-description'>
        Welcome! This digital platform streamlines Draft Audit Report (DAR) collection, processing and compilation from Audit Groups for MCM 
         purpose using AI-powered data extraction.
    </div>
    """, unsafe_allow_html=True)

    # Check if Google services are available and show user info
    if 'google_credentials' in st.session_state and st.session_state.drive_service:
        user_info = get_user_info(st.session_state.drive_service)
        if user_info:
            st.markdown(f"""
            <div class='google-user-info'>
                <h4>🔗 Connected Google Account</h4>
                <p><strong>Name:</strong> {user_info['name']}</p>
                <p><strong>Email:</strong> {user_info['email']}</p>
                <p><small>✅ Files will be stored in this Google Drive account</small></p>
            </div>
            """, unsafe_allow_html=True)
            
            # Add logout button for Google account
            if st.button("🔓 Disconnect Google Account", type="secondary"):
                logout_google_account()
    else:
        st.markdown("""
        <div class='oauth-info'>
            <h4>📁 Personal Google Drive Integration</h4>
            <p>This app stores all files in your personal Google Drive for easy access and management.</p>
            <p><small>You'll be prompted to authorize Google Drive access after login.</small></p>
        </div>
        """, unsafe_allow_html=True)

    # Regular login form
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
            st.session_state.drive_structure_initialized = False
            st.session_state.login_event_logged = False
            st.rerun()
        else:
            st.error("Invalid username or password")