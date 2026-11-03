"""Statisches CSS-Design für eine aufgeräumte Desktop- und Smartphone-Ansicht inkl. Tab-Leiste."""

import streamlit as st

_STATIC_APP_CSS = """
<style>
  .block-container {
      padding-top: 1.8rem;
      padding-bottom: 3rem;
      max-width: 1120px;
  }
  div[data-testid="stMetricValue"] {
      font-size: 1.45rem;
      font-weight: 700;
  }
  /* Zustandsstabile horizontale Tab-Navigation via st.radio */
  div[data-testid="stRadio"] > div[role="radiogroup"] {
      gap: 0.35rem;
      border-bottom: 1px solid rgba(128, 128, 128, 0.25);
      padding-bottom: 0;
      margin-bottom: 0.6rem;
      flex-wrap: wrap;
  }
  div[data-testid="stRadio"] > div[role="radiogroup"] > label {
      background: transparent !important;
      padding: 0.45rem 0.9rem 0.6rem 0.9rem !important;
      margin-right: 0 !important;
      border-radius: 0.35rem 0.35rem 0 0 !important;
      border-bottom: 2px solid transparent;
      cursor: pointer;
      transition: color 0.15s ease, border-color 0.15s ease, background-color 0.15s ease;
  }
  div[data-testid="stRadio"] > div[role="radiogroup"] > label:hover {
      background: rgba(128, 128, 128, 0.08) !important;
  }
  div[data-testid="stRadio"] > div[role="radiogroup"] > label > div:first-child {
      display: none !important;
  }
  div[data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) {
      border-bottom: 2.5px solid #ff4b4b;
      font-weight: 600;
  }
  @media (max-width: 640px) {
      .block-container {
          padding-top: 1rem;
          padding-left: 0.8rem;
          padding-right: 0.8rem;
      }
  }
</style>
"""


def inject_static_styles() -> None:
    """Injiziert ausschließlich konstantes Layout-CSS ohne jegliche Benutzereingaben."""
    st.markdown(_STATIC_APP_CSS, unsafe_allow_html=True)
