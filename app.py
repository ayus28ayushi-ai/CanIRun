"""
app.py — RepoReady Streamlit UI entry point.

This file contains ONLY UI code. All analysis logic lives in repoready/.
"""

import streamlit as st

st.set_page_config(
    page_title="RepoReady",
    page_icon="🔍",
    layout="wide",
)

st.title("🔍 RepoReady")
st.caption("Can I actually run this repository?")

st.divider()

# Persist the last-entered URL across reruns via session state.
if "github_url" not in st.session_state:
    st.session_state.github_url = ""

url = st.text_input(
    label="GitHub repository URL",
    placeholder="https://github.com/owner/repo",
    value=st.session_state.github_url,
    key="github_url",
)

if st.button("Analyze Repository", type="primary"):
    if not url.strip():
        st.warning("Please enter a GitHub URL.")
    else:
        st.info(f"Would analyze: {url}")
