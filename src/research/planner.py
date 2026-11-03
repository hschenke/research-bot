"""Phase 1: Strukturierte Zerlegung eines Themas in präzise Teilfragen und Suchstrategien."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.models import DepthProfile, ResearchJob, ResearchPlan, SubTopicPlan
from src.research.gemini_client import ResearchLLMGateway


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
    return "Deutsch" if lang_code.lower() == "de" else "English"


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

    draft = gateway.generate_structured(
        prompt=prompt,
        system_instruction=system_instruction,
        schema_cls=_DraftResearchPlan,
        prefer_deep=profile.subtopic_count >= 10,
    )

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
        subtopics = [
            SubTopicPlan(
                index=1,
                title=job.topic,
                guiding_question=f"Was sind die wichtigsten aktuellen Erkenntnisse zu {job.topic}?",
                search_queries=[job.topic],
            )
        ]

    return ResearchPlan(
        normalized_title=draft.normalized_title.strip() or job.topic,
        objective_summary=draft.objective_summary.strip(),
        subtopics=subtopics,
    )
