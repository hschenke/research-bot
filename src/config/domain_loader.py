"""Laden, Zusammenführen (Diff/Merge) und sicheres Rendern der Fachlogik-Konfiguration."""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from src.domain.domain_config import DomainConfig

logger = logging.getLogger(__name__)

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
DEFAULT_SETTINGS_YAML_PATH: Path = PROJECT_ROOT / "config" / "settings.yaml"
_PLACEHOLDER_PATTERN = re.compile(r"\{([a-z][a-z0-9_]*)\}")


@functools.lru_cache(maxsize=4)
def load_default_domain_config(yaml_path: Path | None = None) -> DomainConfig:
    """Lädt und validiert die Standard-Fachlogik aus `config/settings.yaml`."""
    target = (yaml_path or DEFAULT_SETTINGS_YAML_PATH).resolve()
    content = target.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    if not isinstance(parsed, dict):
        raise ValueError(f"Konfigurationsdatei '{target}' enthält kein gültiges YAML-Objekt.")
    return DomainConfig.model_validate(parsed)


def deep_merge_dicts(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Führt verschachtelte Konfigurations-Dictionaries rekursiv und seiteneffektfrei zusammen."""
    merged = dict(base)
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dicts(merged[key], val)
        else:
            merged[key] = val
    return merged


def compute_config_overrides(
    default_cfg: DomainConfig,
    custom_cfg: DomainConfig,
) -> dict[str, Any]:
    """Berechnet nur die tatsächlich gegenüber `settings.yaml` veränderten Felder (Diff)."""
    base_dict = default_cfg.model_dump(mode="json")
    custom_dict = custom_cfg.model_dump(mode="json")
    return _diff_nested_dicts(base_dict, custom_dict)


def _diff_nested_dicts(base: dict[str, Any], custom: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key, val in custom.items():
        if key not in base:
            diff[key] = val
        elif isinstance(val, dict) and isinstance(base[key], dict) and key != "report_formats":
            sub_diff = _diff_nested_dicts(base[key], val)
            if sub_diff:
                diff[key] = sub_diff
        elif val != base[key]:
            diff[key] = val
    return diff


def merge_domain_config(
    default_cfg: DomainConfig,
    overrides: dict[str, Any] | None,
) -> DomainConfig:
    """Wendet gespeicherte Web-App-Anpassungen typsicher auf die Datei-Standardwerte an."""
    if not overrides:
        return default_cfg
    base_dict = default_cfg.model_dump(mode="json")
    merged_dict = deep_merge_dicts(base_dict, overrides)
    try:
        return DomainConfig.model_validate(merged_dict)
    except Exception as exc:
        logger.warning("Gespeicherte Fachlogik-Anpassungen ungültig (%s) – nutze Standardwerte.", exc)
        return default_cfg


def dump_domain_config_yaml(config: DomainConfig) -> str:
    """Serialisiert ein `DomainConfig`-Objekt in gut lesbares UTF-8-YAML."""
    payload = config.model_dump(mode="json")
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=110,
    )


def render_prompt_template(
    template: str,
    variables: Mapping[str, str | int | float],
) -> str:
    """
    Ersetzt `{platzhalter}` sicher und absturzfrei ohne Python `.format()`.
    Unbekannte geschweifte Klammern (z. B. JSON-Beispiele im Prompt) bleiben unverändert,
    und Attributzugriffe (Format-String-Injection) sind strukturell ausgeschlossen.
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        return match.group(0)

    return _PLACEHOLDER_PATTERN.sub(_replace, template).strip()
