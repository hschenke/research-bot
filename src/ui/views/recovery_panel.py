"""Kompakte Recovery-Komponente für verwaiste (`stale`) Hintergrund-Jobs im Rules-Tab."""

from __future__ import annotations

import streamlit as st

from src.config.settings import AppSettings
from src.domain.domain_config import DomainConfig
from src.domain.enums import AuthRole
from src.storage.base_repository import ResearchRepository
from src.worker.dispatcher import JobDispatcher


def render_stale_jobs_recovery(
    settings: AppSettings,
    repository: ResearchRepository,
    role: AuthRole,
    cfg: DomainConfig,
) -> None:
    """Zeigt die Anzahl hängender Jobs (`>N Min.`) und erlaubt Admins deren sofortigen Neustart."""
    if role != AuthRole.ADMIN:
        return

    claimable = repository.find_claimable_job_ids(
        stale_timeout_minutes=cfg.stale_job_timeout_minutes,
        limit=10,
    )
    with st.container(border=True):
        c_info, c_btn = st.columns([3.5, 1.3], vertical_alignment="center")
        with c_info:
            st.markdown(
                f"**🛠️ Recovery** · Stale Jobs (>{cfg.stale_job_timeout_minutes}m): **{len(claimable)}**"
            )
        with c_btn:
            if claimable and st.button(
                "🔄 Stale Jobs starten",
                key="btn_recover_stale_jobs",
                type="secondary",
                width="stretch",
            ):
                dispatcher = JobDispatcher(settings)
                for job_id in claimable:
                    job = repository.get_job(job_id)
                    dispatcher.dispatch(job_id, depth=job.depth if job else None)
                st.success(f"{len(claimable)} Job(s) neu gestartet.")
