"""Authentifizierungs- und Sitzungssteuerung für die Streamlit-Oberfläche mit 30-Tage-Cookie."""

from __future__ import annotations

import streamlit as st

from src.config.constants import ADMIN_TOKEN_MAX_AGE_SECONDS, AUTH_COOKIE_NAME
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


def _run_script_iframe(js_code: str) -> None:
    """Führt JavaScript im Browser-Iframe aus (bevorzugt modernes st.iframe)."""
    if hasattr(st, "iframe"):
        st.iframe(js_code, height=1)
    else:
        import streamlit.components.v1 as components

        components.html(js_code, height=0, width=0)


def _inject_auth_cookie_js(token: str) -> None:
    """Schreibt das Auth-Token als Cookie (30 Tage) und in LocalStorage für dauerhafte Anmeldung."""
    js = f"""
    <script>
    (function() {{
        var cookieStr = "{AUTH_COOKIE_NAME}=" + encodeURIComponent("{token}") +
                        "; path=/; max-age={ADMIN_TOKEN_MAX_AGE_SECONDS}; SameSite=Lax";
        try {{ parent.document.cookie = cookieStr; }} catch(e) {{}}
        try {{ document.cookie = cookieStr; }} catch(e) {{}}
        try {{ window.localStorage.setItem("{AUTH_COOKIE_NAME}", "{token}"); }} catch(e) {{}}
    }})();
    </script>
    """
    _run_script_iframe(js)


def _inject_clear_cookie_js() -> None:
    """Löscht das Auth-Cookie und den LocalStorage-Eintrag beim Abmelden."""
    js = f"""
    <script>
    (function() {{
        var expired = "{AUTH_COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax";
        try {{ parent.document.cookie = expired; }} catch(e) {{}}
        try {{ document.cookie = expired; }} catch(e) {{}}
        try {{ window.localStorage.removeItem("{AUTH_COOKIE_NAME}"); }} catch(e) {{}}
    }})();
    </script>
    """
    _run_script_iframe(js)


def _inject_cookie_restore_js() -> None:
    """Stellt eine Sitzung automatisch aus LocalStorage wieder her, falls kein Cookie im Header war."""
    js = f"""
    <script>
    (function() {{
        try {{
            var token = window.localStorage.getItem("{AUTH_COOKIE_NAME}") ||
                        (parent && parent.window.localStorage.getItem("{AUTH_COOKIE_NAME}"));
            if (token && !window.location.search.includes("auth=")) {{
                var url = new URL(window.location.href);
                url.searchParams.set("auth", token);
                window.location.href = url.href;
            }}
        }} catch(e) {{}}
    }})();
    </script>
    """
    _run_script_iframe(js)


def determine_session_role(settings: AppSettings) -> AuthRole:
    """
    Ermittelt die aktuelle Berechtigungsrolle nach dem Prinzip Default-Deny:
    - Ohne `APP_PASSWORD`: lokaler Vollzugriff (`ADMIN`).
    - Mit `APP_PASSWORD`: `ADMIN` (Cookie/Token/Passwort), `READONLY` oder `GUEST`.
    """
    if not settings.app_password:
        return AuthRole.ADMIN

    if st.session_state.get("explicit_logout"):
        return AuthRole.GUEST

    cached_role = st.session_state.get("auth_role")
    if cached_role in {AuthRole.ADMIN, AuthRole.READONLY}:
        return AuthRole(cached_role)

    # 1. Cookie aus Streamlit-Request-Kontext prüfen (automatisch vom Browser übermittelt)
    if hasattr(st, "context") and hasattr(st.context, "cookies"):
        cookie_token = str(st.context.cookies.get(AUTH_COOKIE_NAME, "") or "")
        role = resolve_credential_role(cookie_token, settings.app_password)
        if role == AuthRole.ADMIN:
            st.session_state["auth_role"] = AuthRole.ADMIN
            return AuthRole.ADMIN

    # 2. Signiertes Token aus URL-Query-Parametern prüfen
    query_token = str(st.query_params.get("auth", "") or "")
    role = resolve_credential_role(query_token, settings.app_password)
    if role in {AuthRole.ADMIN, AuthRole.READONLY}:
        st.session_state["auth_role"] = role
        return role

    return AuthRole.GUEST


def render_login_gate_or_sidebar(settings: AppSettings, current_role: AuthRole) -> bool:
    """Rendert die Anmeldemaske bzw. den Sidebar-Status inklusive Cookie-Synchronisation."""
    if not settings.app_password:
        st.sidebar.caption("🔓 Local Mode")
        return True

    if current_role == AuthRole.ADMIN:
        token_to_persist = st.session_state.get("_pending_cookie_token") or st.query_params.get("auth")
        if token_to_persist:
            _inject_auth_cookie_js(token_to_persist)

        st.sidebar.success("🔐 **Admin** · Session aktiv")
        if st.sidebar.button("Logout", width="stretch"):
            _perform_logout()
            st.rerun()
        return True

    if current_role == AuthRole.READONLY:
        st.sidebar.info("👁️ **Read-Only**")
        with st.sidebar.expander("🔑 Admin-Login"):
            _render_password_form(settings, form_key="sidebar_admin_login")
        return True

    if not st.session_state.get("explicit_logout"):
        _inject_cookie_restore_js()

    st.markdown("### 🔐 Login")
    st.caption("Passwort eingeben (30 Tage Session-Cookie).")
    _render_password_form(settings, form_key="gate_admin_login")
    return False


def _render_password_form(settings: AppSettings, form_key: str) -> None:
    limiter = _get_rate_limiter()
    locked, remaining_sec = limiter.is_locked_out()
    if locked:
        st.error(f"Zu viele Versuche. Bitte {remaining_sec}s warten.")
        return

    with st.form(form_key):
        password_input = st.text_input("Passwort", type="password")
        submitted = st.form_submit_button("Login", type="primary", width="stretch")

    if not submitted:
        return

    if resolve_credential_role(password_input, settings.app_password) == AuthRole.ADMIN:
        limiter.reset()
        admin_token = generate_admin_token(settings.app_password)
        st.session_state["auth_role"] = AuthRole.ADMIN
        st.session_state["explicit_logout"] = False
        st.session_state["_pending_cookie_token"] = admin_token
        st.query_params["auth"] = admin_token
        st.rerun()
    else:
        limiter.record_failure()
        st.error("Ungültiges Passwort.")


def _perform_logout() -> None:
    _inject_clear_cookie_js()
    st.session_state["auth_role"] = AuthRole.GUEST
    st.session_state["explicit_logout"] = True
    st.session_state.pop("_pending_cookie_token", None)
    if "auth" in st.query_params:
        del st.query_params["auth"]
