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

  /* Interaktive Recherche-Tabellen & Begriff-Tooltips */
  .surf-tbl-wrap {
      width: 100%;
      margin: 0.6rem 0 0.9rem 0;
      overflow-x: auto;
      overflow-y: visible;
      border-radius: 8px;
      border: 1px solid rgba(128, 128, 128, 0.22);
  }
  .surf-tbl {
      width: 100%;
      border-collapse: collapse;
      font-size: 13.5px;
      line-height: 1.45;
      text-align: left;
  }
  .surf-tbl th {
      background: rgba(128, 128, 128, 0.08);
      font-weight: 600;
      font-size: 12.5px;
      padding: 8px 10px;
      border-bottom: 2px solid rgba(128, 128, 128, 0.25);
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
  .surf-tbl th a:hover {
      color: #0284c7;
  }
  .surf-sort-arrow {
      font-size: 11px;
      opacity: 0.65;
  }
  .surf-tbl td {
      padding: 7px 10px;
      border-bottom: 1px solid rgba(128, 128, 128, 0.12);
      position: relative;
      overflow: visible;
      vertical-align: middle;
  }
  .surf-tbl tbody tr:last-child td {
      border-bottom: none;
  }
  .surf-tbl tbody tr:nth-child(even) {
      background-color: rgba(128, 128, 128, 0.025);
  }
  .surf-tbl tbody tr:hover {
      background-color: rgba(2, 132, 199, 0.05);
  }
  .surf-tip-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 15px;
      height: 15px;
      min-width: 15px;
      border-radius: 50%;
      background: rgba(2, 132, 199, 0.12);
      color: #0284c7;
      border: 1px solid rgba(2, 132, 199, 0.45);
      font-size: 10px;
      font-weight: bold;
      font-family: inherit;
      line-height: 1;
      margin-left: 4px;
      cursor: pointer;
      position: relative;
      vertical-align: middle;
      outline: none;
      user-select: none;
      transition: all 0.15s ease;
  }
  .surf-tip-icon:hover,
  .surf-tip-icon:focus,
  .surf-tip-icon:active {
      background: #0284c7;
      color: #ffffff;
      border-color: #0284c7;
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
      font-weight: normal;
      line-height: 1.4;
      width: max-content;
      max-width: 250px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
      pointer-events: none;
      opacity: 0;
      visibility: hidden;
      transition: opacity 0.15s ease, visibility 0.15s ease;
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
  @media (max-width: 600px) {
      .surf-tip-pop {
          max-width: 200px;
          font-size: 11px;
          padding: 6px 10px;
      }
  }
</style>
"""


def inject_static_styles() -> None:
    """Injiziert ausschließlich konstantes Layout-CSS ohne jegliche Benutzereingaben."""
    st.markdown(_STATIC_APP_CSS, unsafe_allow_html=True)
