"""Statisches CSS-Designsystem für eine moderne, schnelle und aufgeräumte Desktop- & Mobile-UI."""

import streamlit as st

_STATIC_APP_CSS = """
<style>
  /* Ausreichend Abstand unter der fixierten Streamlit-Top-Bar (verhindert Abschneiden auf Mobile & Desktop) */
  .block-container {
      padding-top: 3.75rem !important;
      padding-bottom: 2.5rem !important;
      max-width: 1080px;
  }
  footer, div[data-testid="stDecoration"] {
      display: none !important;
  }
  div[data-testid="stMetricValue"] {
      font-size: 1.35rem;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
  }

  /* Instant Response: Kein Dimming/Fade bei Reruns */
  [data-stale="true"],
  .element-container[data-stale="true"],
  div[data-testid="stAppViewContainer"] [data-stale="true"] {
      opacity: 1 !important;
      transition: none !important;
  }

  /* Kompakte Top-Header-Leiste */
  .rb-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      margin-top: 0.15rem;
      margin-bottom: 0.45rem;
      padding-bottom: 0.15rem;
  }
  .rb-brand {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      font-size: 1.38rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      line-height: 1.25;
  }
  .rb-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      font-size: 0.75rem;
      font-weight: 600;
      padding: 0.18rem 0.55rem;
      border-radius: 999px;
      background: rgba(37, 99, 235, 0.1);
      color: #2563eb;
      border: 1px solid rgba(37, 99, 235, 0.22);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
  }

  /* Pill-Statusleiste (Routing & Report-Meta) */
  .rb-pill-bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.35rem;
      margin: 0.15rem 0 0.35rem 0;
  }
  .rb-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      font-size: 0.76rem;
      font-weight: 500;
      padding: 0.18rem 0.52rem;
      border-radius: 6px;
      background: rgba(128, 128, 128, 0.08);
      border: 1px solid rgba(128, 128, 128, 0.16);
      white-space: nowrap;
  }

  /* Moderne Tab-Navigation */
  div[data-baseweb="tab-list"] {
      gap: 0.2rem;
      border-bottom: 1px solid rgba(128, 128, 128, 0.2);
      margin-bottom: 0.65rem;
  }
  button[data-baseweb="tab"] {
      font-size: 0.88rem;
      font-weight: 500;
      padding: 0.4rem 0.75rem 0.5rem 0.75rem;
      border-radius: 0.4rem 0.4rem 0 0;
      transition: background-color 0.12s ease, color 0.12s ease;
  }
  button[data-baseweb="tab"]:hover {
      background: rgba(37, 99, 235, 0.06);
  }
  button[data-baseweb="tab"][aria-selected="true"] {
      font-weight: 600;
  }

  /* Linke Sidebar-Navigation kompakt & linksbündig */
  section[data-testid="stSidebar"] [data-testid="stButton"] button {
      justify-content: flex-start !important;
      text-align: left !important;
      padding: 0.4rem 0.75rem !important;
      min-height: 2.25rem !important;
      font-size: 0.88rem !important;
  }
  section[data-testid="stSidebar"] [data-testid="stButton"] button > div {
      justify-content: flex-start !important;
      text-align: left !important;
  }

  /* Kompakte Aktions-Buttons (.md, .html, E-Mail, Delete, Stop, Report) immer klein & nebeneinander */
  div[data-testid="stHorizontalBlock"]:has([data-testid="stDownloadButton"]),
  div[data-testid="stColumn"] div[data-testid="stHorizontalBlock"] {
      display: flex !important;
      flex-direction: row !important;
      flex-wrap: wrap !important;
      gap: 0.4rem !important;
      align-items: center !important;
      justify-content: flex-start !important;
      margin: 0.15rem 0 0.45rem 0 !important;
  }
  div[data-testid="stHorizontalBlock"]:has([data-testid="stDownloadButton"]) > div[data-testid="stColumn"],
  div[data-testid="stColumn"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
      width: auto !important;
      min-width: 0 !important;
      flex: 0 0 auto !important;
  }
  div[data-testid="stHorizontalBlock"]:has([data-testid="stDownloadButton"]) button,
  div[data-testid="stColumn"] div[data-testid="stHorizontalBlock"] button {
      padding: 0.22rem 0.62rem !important;
      min-height: 1.95rem !important;
      font-size: 0.8rem !important;
      line-height: 1.2 !important;
      width: auto !important;
      white-space: nowrap !important;
  }

  /* Proportionale Überschriften in Reports (kein riesiger 4-Zeilen-Titel auf Mobile) */
  .block-container h1 {
      font-size: 1.42rem !important;
      line-height: 1.28 !important;
      margin-top: 0.1rem !important;
      padding-top: 0 !important;
  }
  .block-container h2 {
      font-size: 1.15rem !important;
      line-height: 1.3 !important;
      margin-top: 0.85rem !important;
  }
  .block-container h3 {
      font-size: 1.02rem !important;
      line-height: 1.3 !important;
  }

  /* Karten, Formulare & Buttons */
  div[data-testid="stForm"],
  div[data-testid="stVerticalBlockBorderWrapper"] {
      border-radius: 10px !important;
      border-color: rgba(128, 128, 128, 0.18) !important;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
  }
  div.stButton > button,
  div.stDownloadButton > button,
  div.stFormSubmitButton > button {
      border-radius: 8px;
      font-weight: 500;
      transition: transform 0.1s ease, box-shadow 0.12s ease, border-color 0.12s ease;
  }

  @media (max-width: 640px) {
      .block-container {
          padding-top: 3.5rem !important;
          padding-left: 0.75rem !important;
          padding-right: 0.75rem !important;
      }
      .rb-brand { font-size: 1.2rem; }
      .rb-pill {
          font-size: 0.71rem;
          padding: 0.14rem 0.42rem;
      }
      div[data-baseweb="tab-list"] {
          gap: 0.08rem;
      }
      button[data-baseweb="tab"] {
          font-size: 0.76rem;
          padding: 0.32rem 0.42rem 0.42rem 0.42rem;
      }
      .block-container h1 {
          font-size: 1.22rem !important;
      }
      .block-container h2 {
          font-size: 1.06rem !important;
      }
  }

  /* Interaktive Tabellen & Tooltips */
  .surf-tbl-wrap {
      width: 100%;
      margin: 0.5rem 0 0.75rem 0;
      overflow-x: auto;
      overflow-y: visible;
      border-radius: 10px;
      border: 1px solid rgba(128, 128, 128, 0.2);
      box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);
  }
  .surf-tbl {
      width: 100%;
      border-collapse: collapse;
      font-size: 13.5px;
      line-height: 1.45;
      text-align: left;
      font-variant-numeric: tabular-nums;
  }
  .surf-tbl th {
      background: rgba(128, 128, 128, 0.07);
      font-weight: 600;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      padding: 9px 11px;
      border-bottom: 1px solid rgba(128, 128, 128, 0.24);
      white-space: nowrap;
      user-select: none;
  }
  .surf-tbl th a {
      color: inherit;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 4px;
  }
  .surf-tbl th a:hover { color: #2563eb; }
  .surf-sort-arrow { font-size: 11px; opacity: 0.65; }
  .surf-tbl td {
      padding: 8px 11px;
      border-bottom: 1px solid rgba(128, 128, 128, 0.11);
      position: relative;
      overflow: visible;
      vertical-align: middle;
  }
  .surf-tbl tbody tr:last-child td { border-bottom: none; }
  .surf-tbl tbody tr:nth-child(even) { background-color: rgba(128, 128, 128, 0.02); }
  .surf-tbl tbody tr:hover { background-color: rgba(37, 99, 235, 0.05); }
  .surf-tip-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 15px;
      height: 15px;
      min-width: 15px;
      border-radius: 50%;
      background: rgba(37, 99, 235, 0.1);
      color: #2563eb;
      border: 1px solid rgba(37, 99, 235, 0.4);
      font-size: 10px;
      font-weight: 700;
      line-height: 1;
      margin-left: 4px;
      cursor: pointer;
      position: relative;
      vertical-align: middle;
      outline: none;
      user-select: none;
      transition: all 0.14s ease;
  }
  .surf-tip-icon:hover, .surf-tip-icon:focus, .surf-tip-icon:active {
      background: #2563eb;
      color: #ffffff;
      border-color: #2563eb;
  }
  .surf-tip-pop {
      position: absolute;
      bottom: calc(100% + 7px);
      left: 50%;
      transform: translateX(-50%);
      background: #0f172a;
      color: #f8fafc;
      padding: 8px 11px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 400;
      line-height: 1.4;
      width: max-content;
      max-width: 250px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
      pointer-events: none;
      opacity: 0;
      visibility: hidden;
      transition: opacity 0.14s ease, visibility 0.14s ease;
      z-index: 9999;
      text-align: left;
      white-space: normal;
  }
  .surf-tip-pop::after {
      content: "";
      position: absolute;
      top: 100%;
      left: 50%;
      transform: translateX(-50%);
      border-width: 5px;
      border-style: solid;
      border-color: #0f172a transparent transparent transparent;
  }
  .surf-tip-icon:hover .surf-tip-pop,
  .surf-tip-icon:focus .surf-tip-pop,
  .surf-tip-icon:active .surf-tip-pop {
      opacity: 1;
      visibility: visible;
      pointer-events: auto;
  }
</style>
"""


def inject_static_styles() -> None:
    """Injiziert ausschließlich konstantes Layout-CSS ohne jegliche Benutzereingaben."""
    st.markdown(_STATIC_APP_CSS, unsafe_allow_html=True)


