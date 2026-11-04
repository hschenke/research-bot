"""Phase 1: Strukturierte Zerlegung eines Themas in präzise Teilfragen inkl. Direktplan-Fallback."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from src.domain.exceptions import JobCancelledError, ResearchBotError
from src.domain.models import DepthProfile, ResearchJob, ResearchPlan, SubTopicPlan
from src.research.gemini_client import ResearchLLMGateway

logger = logging.getLogger(__name__)


class _DraftSubTopic(BaseModel):
    title: str = Field(description="Prägnanter Titel des Teilgebiets.")
    guiding_question: str = Field(description="Konkrete Leitfrage für dieses Teilgebiet.")
    search_queries: list[str] = Field(
        description="2 bis 4 präzise Web-Suchanfragen zur Beantwortung der Leitfrage."
    )


class _DraftResearchPlan(BaseModel):
    normalized_title: str = Field(description="Professioneller Gesamttitel des Berichts.")
    objective_summary: str = Field(description="Kurze Zielbeschreibung der Untersuchung in 2 Sätzen.")
    subtopics: list[_DraftSubTopic] = Field(description="Geordnete, überschneidungsfreie Teilthemen.")


def _language_label(lang_code: str) -> str:
    return "Deutsch" if lang_code.lower() == "de" else "US English"


def _build_deterministic_fallback_plan(job: ResearchJob, profile: DepthProfile) -> ResearchPlan:
    """Erstellt bei erschöpftem KI-Kontingent sofort einen sauberen Notfall-Rechercheplan."""
    topic = job.topic.strip()
    is_de = job.language.lower() == "de"
    templates = (
        [
            ("Grundlagen, Fakten & aktueller Stand", f"Was sind die wichtigsten aktuellen Fakten zu {topic}?", [topic, f"{topic} Überblick Fakten"]),
            ("Vergleich, Kriterien & Top-Empfehlungen", f"Welche Optionen, Regionen oder Ansätze schneiden bei {topic} am besten ab?", [f"{topic} Vergleich Empfehlung", f"{topic} Kriterien"]),
            ("Besonderheiten, Risiken & Praxistipps", f"Welche Rahmenbedingungen, Risiken oder Praxistipps gelten für {topic}?", [f"{topic} Tipps Hinweise", f"{topic} Risiken"]),
        ]
        if is_de
        else [
            ("Core Facts & Current State", f"What are the most important current facts about {topic}?", [topic, f"{topic} overview facts"]),
            ("Comparison, Criteria & Top Options", f"Which options or approaches perform best regarding {topic}?", [f"{topic} comparison best options", f"{topic} criteria"]),
            ("Key Considerations, Risks & Practical Tips", f"What risks, limitations, or practical tips apply to {topic}?", [f"{topic} tips guide", f"{topic} risks"]),
        ]
    )
    count = max(1, min(profile.subtopic_count, len(templates)))
    subtopics = [
        SubTopicPlan(index=idx, title=t[0], guiding_question=t[1], search_queries=t[2])
        for idx, t in enumerate(templates[:count], start=1)
    ]
    summary = (
        f"Kompakte Untersuchung zu '{topic}' entlang von {count} Kernbereichen."
        if is_de
        else f"Structured investigation of '{topic}' across {count} core areas."
    )
    return ResearchPlan(normalized_title=topic, objective_summary=summary, subtopics=subtopics)


def create_research_plan(
    gateway: ResearchLLMGateway,
    job: ResearchJob,
    profile: DepthProfile,
) -> ResearchPlan:
    """Erzeugt einen auf die gewählte Recherche-Tiefe abgestimmten `ResearchPlan`."""
    lang_name = _language_label(job.language)
    focus_block = (
        f"\nSpezifische Fokus-Vorgaben des Nutzers:\n{job.focus_instructions}\n"
        if job.focus_instructions.strip()
        else ""
    )

    system_instruction = (
        f"Du bist ein leitender Research-Architekt. Antworte ausschließlich auf {lang_name}. "
        "Zerlege komplexe Themen in logisch aufeinander aufbauende, überschneidungsfreie Teilgebiete, "
        "die aktuelle Daten, Fakten, Markt-/Technik-Analysen, Chancen, Risiken und Gegenpositionen abdecken."
    )
    prompt = (
        f"Erstelle einen strukturierten Recherche-Plan zum Thema:\n\"{job.topic}\"\n"
        f"{focus_block}\n"
        f"Erzeuge exakt {profile.subtopic_count} klar abgegrenzte Teilthemen (`subtopics`). "
        "Formuliere pro Teilthema eine präzise Leitfrage sowie 3 gezielte Suchanfragen."
    )

    try:
        draft = gateway.generate_structured(
            prompt=prompt,
            system_instruction=system_instruction,
            schema_cls=_DraftResearchPlan,
            prefer_deep=profile.subtopic_count >= 10,
        )
    except JobCancelledError:
        raise
    except ResearchBotError as exc:
        logger.warning("KI-Planung fehlgeschlagen (%s) – nutze direkten Fallback-Rechercheplan.", exc)
        return _build_deterministic_fallback_plan(job, profile)

    limited_drafts = draft.subtopics[: profile.subtopic_count]
    subtopics = [
        SubTopicPlan(
            index=idx,
            title=item.title.strip(),
            guiding_question=item.guiding_question.strip(),
            search_queries=[q.strip() for q in item.search_queries[:4] if q.strip()],
        )
        for idx, item in enumerate(limited_drafts, start=1)
    ]

    if not subtopics:
        return _build_deterministic_fallback_plan(job, profile)

    return ResearchPlan(
        normalized_title=draft.normalized_title.strip() or job.topic,
        objective_summary=draft.objective_summary.strip(),
        subtopics=subtopics,
    )
