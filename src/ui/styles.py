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

  /* Sofortige Reaktionszeit: Keine künstliche Ausblendung/Fade bei Script-Reruns */
  [data-stale="true"],
  .element-container[data-stale="true"],
  div[data-testid="stAppViewContainer"] [data-stale="true"] {
      opacity: 1 !important;
      transition: none !important;
  }

  /* Moderne, elegante Tab-Navigation */
  div[data-baseweb="tab-list"] {
      gap: 0.35rem;
      border-bottom: 1px solid rgba(128, 128, 128, 0.25);
      margin-bottom: 0.8rem;
  }
  button[data-baseweb="tab"] {
      font-size: 0.95rem;
      font-weight: 500;
      padding: 0.45rem 0.9rem 0.6rem 0.9rem;
      border-radius: 0.35rem 0.35rem 0 0;
      transition: background-color 0.12s ease;
  }
  button[data-baseweb="tab"]:hover {
      background: rgba(128, 128, 128, 0.08);
  }
  button[data-baseweb="tab"][aria-selected="true"] {
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
