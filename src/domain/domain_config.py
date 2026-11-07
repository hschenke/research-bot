"""Typisierte, unveränderliche Datenmodelle für die gesamte editierbare Fach- & KI-Logik."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import ResearchDepth
from src.domain.models import DepthProfile


class ProcessingLimitsConfig(BaseModel):
    """Konfigurierbare Kontext-, Extraktions- und Abfragelimits."""

    model_config = ConfigDict(frozen=True)

    max_search_queries_per_subtopic: int = Field(default=3, ge=1, le=8)
    max_open_questions_per_followup: int = Field(default=2, ge=1, le=5)
    max_chars_per_scraped_page: int = Field(default=3500, ge=500, le=15000)
    max_finding_chars_in_synthesis: int = Field(default=1800, ge=300, le=10000)
    max_baseline_chars_in_context: int = Field(default=2200, ge=300, le=10000)
    max_takeaways_count: int = Field(default=5, ge=1, le=12)


class StyleRulesConfig(BaseModel):
    """Editierbare Redaktions-, Kürze- und Format-Override-Regeln."""

    model_config = ConfigDict(frozen=True)

    global_style_rules: str = Field(min_length=10, max_length=6000)
    format_override_rules: str = Field(min_length=10, max_length=6000)
    compact_list_keywords: list[str] = Field(default_factory=list, max_length=50)
    include_sources_section: bool = True
    sources_heading_de: str = Field(default="## Quellen", min_length=2, max_length=120)
    sources_heading_en: str = Field(default="## Sources", min_length=2, max_length=120)
    partial_notice_de: str = Field(min_length=10, max_length=500)
    partial_notice_en: str = Field(min_length=10, max_length=500)


class ReportFormatPreset(BaseModel):
    """Konfigurierbares Berichtsformat-Profil (z. B. Kompakt, Knappe Liste, Tabelle, Dossier)."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=2, max_length=120)
    instructions: str = Field(min_length=10, max_length=4000)
    max_subtopics_cap: int = Field(default=0, ge=0, le=20)
    omit_wrapper_sections: bool = False


class DepthProfileConfig(BaseModel):
    """Editierbares Profil einer Recherche-Tiefe."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(min_length=2, max_length=120)
    estimated_duration_label: str = Field(min_length=2, max_length=80)
    subtopic_count: int = Field(ge=1, le=25)
    target_word_range: str = Field(min_length=2, max_length=80)
    max_subtopic_bullets: int = Field(default=5, ge=1, le=15)
    max_urls_per_subtopic: int = Field(ge=0, le=10)
    followup_rounds: int = Field(ge=0, le=4)
    parallel_subtopics: int = Field(default=2, ge=1, le=6)
    use_deep_research_agent: bool = False
    deep_agent_name: str = Field(default="", max_length=120)
    prefer_deep_model: bool = False
    inter_step_pause_seconds: float = Field(default=0.0, ge=0.0, le=10.0)

    def to_depth_profile(self, depth: ResearchDepth) -> DepthProfile:
        """Konvertiert die Konfiguration in das Laufzeit-`DepthProfile`."""
        return DepthProfile(
            depth=depth,
            label=self.label,
            estimated_duration_label=self.estimated_duration_label,
            subtopic_count=self.subtopic_count,
            max_urls_per_subtopic=self.max_urls_per_subtopic,
            followup_rounds=self.followup_rounds,
            use_deep_research_agent=self.use_deep_research_agent,
            deep_agent_name=self.deep_agent_name,
            inter_step_pause_seconds=self.inter_step_pause_seconds,
            target_word_range=self.target_word_range,
            max_subtopic_bullets=self.max_subtopic_bullets,
            parallel_subtopics=self.parallel_subtopics,
            prefer_deep_model=self.prefer_deep_model,
        )


class PromptTemplatesConfig(BaseModel):
    """Vollständig editierbare System- und User-Prompts aller Pipeline-Phasen."""

    model_config = ConfigDict(frozen=True)

    planner_system_prompt: str = Field(min_length=10, max_length=8000)
    planner_user_prompt: str = Field(min_length=10, max_length=8000)
    deep_baseline_prompt: str = Field(min_length=10, max_length=8000)
    deep_baseline_fallback_system_prompt: str = Field(min_length=10, max_length=4000)
    investigator_quick_system_prompt: str = Field(min_length=10, max_length=8000)
    investigator_quick_user_prompt: str = Field(min_length=10, max_length=8000)
    investigator_deep_system_prompt: str = Field(min_length=10, max_length=8000)
    investigator_deep_user_prompt: str = Field(min_length=10, max_length=8000)
    investigator_consolidation_system_prompt: str = Field(min_length=10, max_length=8000)
    investigator_consolidation_user_prompt: str = Field(min_length=10, max_length=8000)
    investigator_followup_user_prompt: str = Field(min_length=10, max_length=8000)
    synthesizer_system_prompt: str = Field(min_length=10, max_length=8000)
    synthesizer_user_prompt: str = Field(min_length=10, max_length=10000)


class FallbackSubtopicTemplate(BaseModel):
    """Notfall-Teilthemen-Vorlage bei nicht verfügbarem Planungs-LLM."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=2, max_length=150)
    guiding_question: str = Field(min_length=5, max_length=300)
    search_queries: list[str] = Field(min_length=1, max_length=6)


class FallbackTemplatesConfig(BaseModel):
    """Sprachspezifische Notfall-Vorlagen für den Recherche-Plan."""

    model_config = ConfigDict(frozen=True)

    de: list[FallbackSubtopicTemplate] = Field(min_length=1, max_length=10)
    en: list[FallbackSubtopicTemplate] = Field(min_length=1, max_length=10)


class ModelRoutingConfig(BaseModel):
    """Editierbare Routing-Strategie für KI-Modelle, Tools, Fallbacks und Rate-Limits."""

    model_config = ConfigDict(frozen=True)

    planning_model: str = Field(default="gemini-3.5-flash-lite", min_length=1, max_length=100)
    research_primary_model: str = Field(default="gemini-3.5-flash-lite", min_length=1, max_length=100)
    research_fallback_model: str = Field(default="gemini-3.1-flash-lite", min_length=1, max_length=100)
    synthesizer_model: str = Field(default="gemini-3.5-flash-lite", min_length=1, max_length=100)
    max_rpm: int = Field(default=12, ge=1, le=60)
    min_call_interval_seconds: float = Field(default=4.5, ge=0.0, le=30.0)
    single_pass_research: bool = True
    enable_google_search: bool = True
    enable_duckduckgo_search: bool = True
    enable_reddit_search: bool = True
    enable_hackernews_search: bool = True
    enable_wikipedia_search: bool = True
    enable_arxiv_search: bool = False
    enable_jina_reader: bool = True
    enable_browserbase: bool = False
    enable_cross_verification: bool = True


class DomainConfig(BaseModel):
    """Zentrales, in der Web-App sicht- und editierbares Fachlogik-Objekt."""

    model_config = ConfigDict(frozen=True)

    default_language: str = Field(default="de", pattern=r"^(de|en)$")
    default_depth: ResearchDepth = Field(default=ResearchDepth.STANDARD)
    default_report_format: str = Field(default="compact", min_length=1, max_length=40)
    default_model: str = Field(default="gemini-3.5-flash-lite", min_length=1, max_length=100)
    available_models: list[str] = Field(default_factory=lambda: ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"], min_length=1)
    enable_deep_research_agent: bool = True
    max_concurrent_local_jobs: int = Field(default=4, ge=1, le=16)
    stale_job_timeout_minutes: int = Field(default=5, ge=1, le=180)

    model_routing: ModelRoutingConfig = Field(default_factory=ModelRoutingConfig)
    limits: ProcessingLimitsConfig
    style: StyleRulesConfig
    report_formats: dict[str, ReportFormatPreset] = Field(min_length=1)
    depth_profiles: dict[ResearchDepth, DepthProfileConfig]
    prompts: PromptTemplatesConfig
    fallback_plan_templates: FallbackTemplatesConfig

    def get_depth_profile(self, depth: ResearchDepth) -> DepthProfile:
        """Liefert das aktive `DepthProfile` für die angeforderte Recherche-Tiefe."""
        cfg = self.depth_profiles.get(depth) or next(iter(self.depth_profiles.values()))
        return cfg.to_depth_profile(depth)

    def to_depth_profiles_map(self) -> dict[ResearchDepth, DepthProfile]:
        """Erstellt das vollständige `ResearchDepth -> DepthProfile`-Mapping."""
        return {depth: self.get_depth_profile(depth) for depth in ResearchDepth if depth in self.depth_profiles}

    def is_compact_list_triggered(self, topic: str, focus_instructions: str, format_key: str) -> bool:
        """Prüft, ob durch Auswahl oder Schlüsselwörter im Text eine knappe Liste verlangt wird."""
        if format_key == "list":
            return True
        combined = f"{topic}\n{focus_instructions}".lower()
        return any(kw.strip().lower() in combined for kw in self.style.compact_list_keywords if kw.strip())

    def resolve_effective_format(
        self,
        topic: str,
        focus_instructions: str,
        format_key: str,
    ) -> tuple[str, ReportFormatPreset]:
        """Ermittelt das wirksame `ReportFormatPreset` inkl. automatischer Listen-Erkennung."""
        if self.is_compact_list_triggered(topic, focus_instructions, format_key) and "list" in self.report_formats:
            return "list", self.report_formats["list"]
        if format_key in self.report_formats:
            return format_key, self.report_formats[format_key]
        fallback_key = (
            self.default_report_format
            if self.default_report_format in self.report_formats
            else next(iter(self.report_formats))
        )
        return fallback_key, self.report_formats[fallback_key]
