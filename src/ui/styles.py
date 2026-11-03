"""Statisches CSS-Design für eine aufgeräumte Desktop- und Smartphone-Ansicht."""

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
