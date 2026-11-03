"""Security-Tests: SSRF-Guard, DNS-Rebinding, Path-Traversal, Input-Validierung, Auth & Secret-Hygiene."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.config.settings import AppSettings
from src.domain.enums import AuthRole, ResearchDepth
from src.domain.exceptions import (
    InputValidationError,
    SecurityViolationError,
    SSRFBlockedError,
)
from src.security.auth import (
    LoginRateLimiter,
    generate_admin_token,
    generate_readonly_token,
    resolve_credential_role,
    verify_admin_token,
    verify_readonly_token,
)
from src.security.sanitization import (
    resolve_safe_path,
    sanitize_display_url,
    sanitize_markdown_links,
    validate_email_address,
    validate_research_request,
)
from src.security.ssrf_guard import is_public_ip_address, validate_safe_external_url


class TestSSRFGuard:
    @pytest.mark.parametrize(
        "blocked_ip",
        [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.5.4",
            "192.168.1.100",
            "169.254.169.254",
            "100.64.0.1",
            "0.0.0.0",
            "::1",
            "fe80::1",
            "::ffff:127.0.0.1",
            "::ffff:169.254.169.254",
        ],
    )
    def test_blocks_private_loopback_and_metadata_ips(self, blocked_ip: str) -> None:
        assert is_public_ip_address(blocked_ip) is False

    def test_allows_global_public_ip(self) -> None:
        assert is_public_ip_address("93.184.216.34") is True
        assert is_public_ip_address("2606:2800:220:1:248:1893:25c8:1946") is True

    @pytest.mark.parametrize(
        "unsafe_url",
        [
            "file:///etc/passwd",
            "ftp://example.com/data.txt",
            "javascript:alert(1)",
            "http://localhost/admin",
            "http://127.0.0.1/secret",
            "http://169.254.169.254/latest/meta-data/",
            "https://metadata.google.internal/computeMetadata/v1/",
            "https://service.internal/api",
            "https://user:pass@example.com/path",
            "https://example.com:8080/internal",
        ],
    )
    def test_rejects_unsafe_urls_before_network_call(self, unsafe_url: str) -> None:
        with pytest.raises(SSRFBlockedError):
            validate_safe_external_url(
                unsafe_url,
                dns_resolver=lambda _h, _p: ["93.184.216.34"],
            )

    def test_blocks_dns_rebinding_to_private_network(self) -> None:
        with pytest.raises(SSRFBlockedError):
            validate_safe_external_url(
                "https://evil-rebind.example.org/article",
                dns_resolver=lambda _h, _p: ["93.184.216.34", "10.12.0.5"],
            )

    def test_accepts_valid_https_public_url(self) -> None:
        result = validate_safe_external_url(
            "https://example.com/research/paper",
            dns_resolver=lambda _h, _p: ["93.184.216.34"],
        )
        assert result == "https://example.com/research/paper"


class TestSanitizationAndValidation:
    def test_blocks_path_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityViolationError):
            resolve_safe_path(tmp_path, "../../outside_secret.txt")

    def test_resolves_valid_nested_path(self, tmp_path: Path) -> None:
        resolved = resolve_safe_path(tmp_path, "data/research_bot.db")
        assert resolved.is_relative_to(tmp_path.resolve())

    def test_sanitizes_xss_urls_and_markdown_links(self) -> None:
        assert sanitize_display_url("javascript:alert(1)") == "#"
        assert sanitize_display_url("data:text/html,<script>alert(1)</script>") == "#"
        assert sanitize_display_url("https://example.com/clean") == "https://example.com/clean"

        dirty_md = "Siehe [Böser Link](javascript:alert(1)) und [Guter Link](https://example.com/ok)."
        cleaned_md = sanitize_markdown_links(dirty_md)
        assert "javascript:" not in cleaned_md
        assert "[Guter Link](https://example.com/ok)" in cleaned_md

    def test_validates_email_and_research_request(self) -> None:
        assert validate_email_address("user.name@domain.de") == "user.name@domain.de"
        assert validate_email_address("") == ""

        with pytest.raises(InputValidationError):
            validate_email_address("kein_at_zeichen.de")
        with pytest.raises(InputValidationError):
            validate_research_request(topic="ab")  # zu kurz

        req = validate_research_request(
            topic="  Quantencomputing Fortschritte\x00 2026  ",
            focus_instructions="Hardware & Fehlerkorrektur",
            depth=ResearchDepth.DEEP,
            language="de",
            notify_email="test@firma.de",
        )
        assert req.topic == "Quantencomputing Fortschritte 2026"
        assert req.depth == ResearchDepth.DEEP


class TestAuthenticationAndSecretHygiene:
    def test_admin_and_readonly_tokens_strictly_separated(self) -> None:
        secret_pw = "SuperGeheim!2026"
        admin_tok = generate_admin_token(secret_pw)
        readonly_tok = generate_readonly_token(secret_pw)

        assert verify_admin_token(admin_tok, secret_pw) is True
        assert verify_readonly_token(readonly_tok, secret_pw) is True

        # Readonly-Token darf niemals Admin-Rechte erlangen
        assert verify_admin_token(readonly_tok, secret_pw) is False
        assert resolve_credential_role(readonly_tok, secret_pw) == AuthRole.READONLY
        assert resolve_credential_role(admin_tok, secret_pw) == AuthRole.ADMIN
        assert resolve_credential_role("falsch", secret_pw) == AuthRole.GUEST

    def test_expired_admin_token_is_rejected(self) -> None:
        secret_pw = "SuperGeheim!2026"
        old_token = generate_admin_token(secret_pw, issued_at=1_000_000)
        assert verify_admin_token(old_token, secret_pw) is False

    def test_rate_limiter_locks_out_brute_force(self) -> None:
        limiter = LoginRateLimiter(max_attempts=3, lockout_seconds=60)
        assert limiter.is_locked_out("ip1")[0] is False
        limiter.record_failure("ip1")
        limiter.record_failure("ip1")
        assert limiter.is_locked_out("ip1")[0] is False
        limiter.record_failure("ip1")
        locked, remaining = limiter.is_locked_out("ip1")
        assert locked is True
        assert remaining > 0
        limiter.reset("ip1")
        assert limiter.is_locked_out("ip1")[0] is False

    def test_app_settings_never_expose_secrets_in_repr(self) -> None:
        settings = AppSettings(
            gemini_api_key="AIza_SUPER_SECRET_GEMINI",
            turso_auth_token="ey_SUPER_SECRET_TURSO",
            resend_api_key="re_SUPER_SECRET_RESEND",
            github_token="ghp_SUPER_SECRET_GITHUB",
            app_password="MY_SECRET_PASSWORD",
            smtp_password="MY_SMTP_PASSWORD",
        )
        rendered = repr(settings)
        assert "AIza_SUPER_SECRET_GEMINI" not in rendered
        assert "ey_SUPER_SECRET_TURSO" not in rendered
        assert "re_SUPER_SECRET_RESEND" not in rendered
        assert "ghp_SUPER_SECRET_GITHUB" not in rendered
        assert "MY_SECRET_PASSWORD" not in rendered
        assert "MY_SMTP_PASSWORD" not in rendered
