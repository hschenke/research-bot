"""Strikte Sicherheits- und Whitelist-Validierung für editierbare Fachlogik-Konfigurationen."""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError

from src.config.constants import MAX_CONFIG_YAML_BYTES
from src.domain.domain_config import DomainConfig
from src.domain.enums import ResearchDepth
from src.domain.exceptions import InputValidationError
from src.security.sanitization import (
    strip_control_characters,
    validate_format_key,
    validate_model_identifier,
)


def _sanitize_nested_strings(value: Any) -> Any:
    """Bereinigt rekursiv alle Textwerte einer Konfigurationsstruktur von Steuerzeichen."""
    if isinstance(value, str):
        return strip_control_characters(value)
    if isinstance(value, list):
        return [_sanitize_nested_strings(item) for item in value]
    if isinstance(value, dict):
        return {strip_control_characters(str(k)): _sanitize_nested_strings(v) for k, v in value.items()}
    return value


def validate_domain_config_payload(raw_data: dict[str, Any]) -> DomainConfig:
    """Validiert ein Konfigurations-Dictionary gegen Schema-, Modell- und DoS-Grenzen."""
    cleaned = _sanitize_nested_strings(raw_data)
    try:
        config = DomainConfig.model_validate(cleaned)
    except ValidationError as exc:
        first_err = exc.errors()[0] if exc.errors() else {"loc": ("config",), "msg": str(exc)}
        loc_str = ".".join(str(p) for p in first_err.get("loc", ()))
        raise InputValidationError(
            f"Ungültige Fachlogik-Einstellung bei '{loc_str}': {first_err.get('msg')}"
        ) from exc

    cleaned_models: list[str] = []
    for raw_model in config.available_models:
        valid_m = validate_model_identifier(raw_model, fallback="")
        if valid_m and valid_m not in cleaned_models:
            cleaned_models.append(valid_m)
    if not cleaned_models:
        raise InputValidationError("Mindestens ein gültiges KI-Modell muss freigegeben sein.")

    default_model = validate_model_identifier(config.default_model, fallback=cleaned_models[0])
    if default_model not in cleaned_models:
        cleaned_models.insert(0, default_model)

    for fmt_key in config.report_formats:
        validate_format_key(fmt_key)

    if config.default_report_format not in config.report_formats:
        raise InputValidationError(
            f"Standard-Berichtsformat '{config.default_report_format}' existiert nicht in 'report_formats'."
        )

    missing_depths = [d.value for d in ResearchDepth if d not in config.depth_profiles]
    if missing_depths:
        raise InputValidationError(
            f"Es müssen alle 4 Recherche-Tiefen definiert sein (fehlend: {', '.join(missing_depths)})."
        )

    for depth_cfg in config.depth_profiles.values():
        if depth_cfg.deep_agent_name.strip():
            validate_model_identifier(depth_cfg.deep_agent_name.strip())

    return config.model_copy(
        update={
            "available_models": cleaned_models,
            "default_model": default_model,
        }
    )


def parse_and_validate_domain_yaml(raw_yaml: str) -> DomainConfig:
    """Parst und validiert einen vom Admin im Web-Editor bearbeiteten YAML-Text sicher."""
    encoded_len = len(raw_yaml.encode("utf-8"))
    if encoded_len > MAX_CONFIG_YAML_BYTES:
        raise InputValidationError(
            f"YAML-Konfiguration überschreitet das Sicherheitslimit ({MAX_CONFIG_YAML_BYTES} Bytes)."
        )
    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise InputValidationError(f"YAML-Syntaxfehler: {exc}") from exc

    if not isinstance(parsed, dict):
        raise InputValidationError("Die YAML-Konfiguration muss auf oberster Ebene ein Objekt (Key-Value) sein.")

    return validate_domain_config_payload(parsed)
