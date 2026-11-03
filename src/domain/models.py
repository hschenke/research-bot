"""Typisierte, unveränderliche Datenmodelle für Aufträge, Pläne, Quellen und Berichte."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import JobPhase, JobStatus, ResearchDepth


@dataclass(frozen=True)
class DepthProfile:
    """Konfigurationsprofil für eine Recherche-Tiefe."""

    depth: ResearchDepth
    label: str
    estimated_duration_label: str
    subtopic_count: int
    max_urls_per_subtopic: int
    followup_rounds: int
    use_deep_research_agent: bool
    deep_agent_name: str
    inter_step_pause_seconds: float


class ResearchRequest(BaseModel):
    """Validierte Eingabe zum Anlegen eines neuen Recherche-Auftrags."""

    model_config = ConfigDict(frozen=True)

    topic: str = Field(min_length=3, max_length=300)
    focus_instructions: str = Field(default="", max_length=2000)
    depth: ResearchDepth = Field(default=ResearchDepth.STANDARD)
    language: str = Field(default="de", pattern=r"^(de|en)$")
    notify_email: str = Field(default="", max_length=254)


class SourceCitation(BaseModel):
    """Verifizierte Web-Quelle mit Titel, URL und kurzem Relevanzkontext."""

    model_config = ConfigDict(frozen=True)

    url: str
    title: str = ""
    snippet: str = ""


class SubTopicPlan(BaseModel):
    """Einzelnes Teilthema innerhalb des generierten Recherche-Plans."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=1)
    title: str
    guiding_question: str
    search_queries: list[str] = Field(default_factory=list)


class ResearchPlan(BaseModel):
    """Strukturierter Gesamtplan für die mehrstufige Web-Recherche."""

    model_config = ConfigDict(frozen=True)

    normalized_title: str
    objective_summary: str
    subtopics: list[SubTopicPlan] = Field(min_length=1)


class SubTopicFinding(BaseModel):
    """Zwischenergebnis (Checkpoint) für ein untersuchtes Teilthema."""

    model_config = ConfigDict(frozen=True)

    subtopic_index: int
    title: str
    analysis_markdown: str
    key_facts: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    citations: list[SourceCitation] = Field(default_factory=list)


class ProgressLogEntry(BaseModel):
    """Zeitgestempelter Fortschrittseintrag für die Live-Ansicht in der Web-App."""

    model_config = ConfigDict(frozen=True)

    timestamp_iso: str
    phase: JobPhase
    message: str


class ResearchJob(BaseModel):
    """Persistenter Zustand eines Recherche-Auftrags in der Datenbank."""

    model_config = ConfigDict(frozen=True)

    id: str
    topic: str
    focus_instructions: str = ""
    depth: ResearchDepth = ResearchDepth.STANDARD
    language: str = "de"
    notify_email: str = ""
    status: JobStatus = JobStatus.QUEUED
    phase: JobPhase = JobPhase.QUEUED
    progress_percent: int = Field(default=0, ge=0, le=100)
    status_message: str = ""
    error_message: str = ""
    cancel_requested: bool = False
    email_sent: bool = False
    worker_id: str = ""
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    heartbeat_at: str | None = None
    progress_log: list[ProgressLogEntry] = Field(default_factory=list)


class ResearchReport(BaseModel):
    """Fertiggestelltes, aufbereitetes Recherche-Ergebnis zu einem Auftrag."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    title: str
    topic: str
    depth: ResearchDepth
    language: str
    executive_summary: str
    key_takeaways: list[str] = Field(default_factory=list)
    content_markdown: str
    citations: list[SourceCitation] = Field(default_factory=list)
    model_used: str = ""
    duration_seconds: int = 0
    created_at: str = ""
