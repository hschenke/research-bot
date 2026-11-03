"""Authentifizierungs- und Sitzungssteuerung für die Streamlit-Oberfläche."""

from __future__ import annotations

import streamlit as st

from src.config.constants import AUTH_COOKIE_NAME
from src.config.settings import AppSettings
from src.domain.enums import AuthRole
from src.security.auth import (
    LoginRateLimiter,
    generate_admin_token,
    resolve_credential_role,
)


@st.cache_resource
def _get_rate_limiter() -> LoginRateLimiter:
    """Prozessweiter Rate-Limiter gegen Brute-Force-Passwortversuche."""
    return LoginRateLimiter()


def determine_session_role(settings: AppSettings) -> AuthRole:
    """
    Ermittelt die aktuelle Berechtigungsrolle nach dem Prinzip Default-Deny:
    - Ohne `APP_PASSWORD`: lokaler Vollzugriff (`ADMIN`).
    - Mit `APP_PASSWORD`: `ADMIN` (Passwort/24h-Token), `READONLY` (E-Mail-Link) oder `GUEST` (gesperrt).
    """
    if not settings.app_password:
        return AuthRole.ADMIN

    if st.session_state.get("explicit_logout"):
        return AuthRole.GUEST

    cached_role = st.session_state.get("auth_role")
    if cached_role in {AuthRole.ADMIN, AuthRole.READONLY}:
        return AuthRole(cached_role)

    # Cookie aus Streamlit-Request-Kontext prüfen (sofern vom Browser gesendet)
    if hasattr(st, "context") and hasattr(st.context, "cookies"):
        cookie_token = str(st.context.cookies.get(AUTH_COOKIE_NAME, "") or "")
        role = resolve_credential_role(cookie_token, settings.app_password)
        if role == AuthRole.ADMIN:
            st.session_state["auth_role"] = AuthRole.ADMIN
            return AuthRole.ADMIN

    # Signiertes Token aus URL-Query-Parametern prüfen (z. B. Direktlink aus E-Mail)
    query_token = str(st.query_params.get("auth", "") or "")
    role = resolve_credential_role(query_token, settings.app_password)
    if role in {AuthRole.ADMIN, AuthRole.READONLY}:
        st.session_state["auth_role"] = role
        return role

    return AuthRole.GUEST


def render_login_gate_or_sidebar(settings: AppSettings, current_role: AuthRole) -> bool:
    """
    Rendert die Anmeldemaske (bei `GUEST`) bzw. den Sidebar-Status.
    Gibt `True` zurück, wenn der Zugriff auf die App freigegeben ist.
    """
    if not settings.app_password:
        st.sidebar.caption("🔓 Lokaler Modus (kein `APP_PASSWORD` gesetzt)")
        return True

    if current_role == AuthRole.ADMIN:
        st.sidebar.success("🔐 Angemeldet als **Administrator**")
        if st.sidebar.button("Abmelden", use_container_width=True):
            _perform_logout()
            st.rerun()
        return True

    if current_role == AuthRole.READONLY:
        st.sidebar.info("👁️ **Lese-Modus** (über signierten E-Mail-Link)")
        with st.sidebar.expander("🔑 Als Admin anmelden"):
            _render_password_form(settings, form_key="sidebar_admin_login")
        return True

    # AuthRole.GUEST -> Vollbild-Login-Schranke (Default Deny)
    st.markdown("## 🔐 Anmeldung erforderlich")
    st.caption("Bitte gib das konfigurierte App-Passwort ein, um auf den Research-Bot zuzugreifen.")
    _render_password_form(settings, form_key="gate_admin_login")
    return False


def _render_password_form(settings: AppSettings, form_key: str) -> None:
    limiter = _get_rate_limiter()
    locked, remaining_sec = limiter.is_locked_out()
    if locked:
        st.error(f"Zu viele Fehlversuche. Bitte {remaining_sec} Sekunden warten.")
        return

    with st.form(form_key):
        password_input = st.text_input("App-Passwort", type="password")
        submitted = st.form_submit_button("Anmelden", type="primary", use_container_width=True)

    if not submitted:
        return

    if resolve_credential_role(password_input, settings.app_password) == AuthRole.ADMIN:
        limiter.reset()
        admin_token = generate_admin_token(settings.app_password)
        st.session_state["auth_role"] = AuthRole.ADMIN
        st.session_state["explicit_logout"] = False
        st.query_params["auth"] = admin_token
        st.rerun()
    else:
        limiter.record_failure()
        st.error("Ungültiges Passwort.")


def _perform_logout() -> None:
    st.session_state["auth_role"] = AuthRole.GUEST
    st.session_state["explicit_logout"] = True
    if "auth" in st.query_params:
        del st.query_params["auth"]
