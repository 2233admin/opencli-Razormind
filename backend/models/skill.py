from sqlalchemy import JSON, Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import TimestampMixin


class Skill(TimestampMixin):
    """A reusable browser skill distilled from an execution trace.

    Identified by (domain, capability). `skill_md` is the human/agent-readable
    SKILL.md card; `elements` holds the structured 9-element spec (see
    backend.skills.distill.ELEMENT_KEYS); `evidence` accumulates closed-loop
    events (distilled / executed / corrected with outcomes) that feed the
    self-evaluation + correction round.
    """

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("domain", "capability", name="uq_skill_domain_capability"),)

    # Identity
    domain: Mapped[str] = mapped_column(String(100), nullable=False)
    capability: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Body
    skill_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Structured 9-element spec: preconditions, procedure, milestones,
    # terminal_conditions, false_terminal_states, recovery_policies,
    # anti_drift_boundaries, red_lines.
    elements: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    source_trace: Mapped[str | None] = mapped_column(String(255), nullable=True)
    distill_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Full journey_trace_v1 of the most recent failing run (2026-07-01 addendum) —
    # self_eval/evidence only ever stored a trace_id + outcome summary, so a human
    # looking at a `correction_proposed` entry had no trace body to actually
    # redistill from later. Overwritten on each fail (v1: most recent only, same
    # "keep it simple" call as re_distill's single-trace redistillation).
    last_failing_trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Lifecycle: draft -> active -> deprecated; version bumps on re-distill.
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
