"""All tables. Staleness of scripts is derived (script_version.steps_revision vs workflow.steps_revision);
single-flight Runs are enforced by the unique `run.active_slot` (1 while active, NULL once finished)."""
import json
import re
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PublicationStatus:
    """Result of opening a Run's final page without signing in (see runtime/publication.py)."""
    PUBLISHED = "published"
    NOT_PUBLIC = "not_public"
    UNREACHABLE = "unreachable"
    ALL = (PUBLISHED, NOT_PUBLIC, UNREACHABLE)


_PUBLICATION_LINE = re.compile("^Publication status: (" + "|".join(PublicationStatus.ALL) + r")\b", re.MULTILINE)
_FINAL_PAGE_LINE = re.compile(r"^Final page: (https?://[^\s<>\"']+)$", re.MULTILINE)


def final_page_url_in(output: str) -> str | None:
    """The address on the last `Final page:` line of a Run Log (a successful script prints it when it ends)."""
    matches = _FINAL_PAGE_LINE.findall(output or "")
    return matches[-1] if matches else None


class RunStatus:
    QUEUED = "queued"
    GENERATING = "generating"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ACTIVE = (QUEUED, GENERATING, RUNNING)
    FINISHED = (SUCCEEDED, FAILED)


class Base(DeclarativeBase):
    pass


class Portal(Base):
    __tablename__ = "portal"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    url: Mapped[str] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_hosts: Mapped[str] = mapped_column(Text, default="")

    @property
    def allowed_hosts(self) -> list[str]:
        """Extra hosts (e.g. a single sign-on provider) the agent may also visit, besides the start URL."""
        return [host.strip() for host in self.extra_hosts.replace("\n", ",").split(",") if host.strip()]

    credentials: Mapped[list["Credential"]] = relationship(back_populates="portal")
    workflows: Mapped[list["Workflow"]] = relationship(back_populates="portal")


class Credential(Base):
    __tablename__ = "credential"
    __table_args__ = (UniqueConstraint("portal_id", "label"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    portal_id: Mapped[int] = mapped_column(ForeignKey("portal.id"))
    label: Mapped[str] = mapped_column(String(120))
    username: Mapped[str] = mapped_column(String(255))
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    portal: Mapped[Portal] = relationship(back_populates="credentials")

    @property
    def has_password(self) -> bool:
        return bool(self.password_encrypted)

    @property
    def has_session(self) -> bool:
        return bool(self.session_encrypted)

    @property
    def usable(self) -> bool:
        """A Credential can only be chosen for a Run if it offers some way to sign in."""
        return self.has_password or self.has_session


class Workflow(Base):
    __tablename__ = "workflow"
    __table_args__ = (UniqueConstraint("portal_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    portal_id: Mapped[int] = mapped_column(ForeignKey("portal.id"))
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    steps_revision: Mapped[int] = mapped_column(Integer, default=1)
    starts_signed_in: Mapped[bool] = mapped_column(Boolean, default=False)

    portal: Mapped[Portal] = relationship(back_populates="workflows")
    steps: Mapped[list["WorkflowStep"]] = relationship(
        back_populates="workflow", order_by="WorkflowStep.position", cascade="all, delete-orphan"
    )
    script_versions: Mapped[list["ScriptVersion"]] = relationship(
        back_populates="workflow", order_by="ScriptVersion.version"
    )

    @property
    def current_script_version(self) -> "ScriptVersion | None":
        """Newest Script Version generated from the present Steps; None means Stale or never generated."""
        fresh = [v for v in self.script_versions if v.steps_revision == self.steps_revision]
        return fresh[-1] if fresh else None


class WorkflowStep(Base):
    __tablename__ = "workflow_step"
    __table_args__ = (UniqueConstraint("workflow_id", "position"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflow.id"))
    position: Mapped[int] = mapped_column(Integer)
    instruction: Mapped[str] = mapped_column(Text)

    workflow: Mapped[Workflow] = relationship(back_populates="steps")


class ScriptVersion(Base):
    __tablename__ = "script_version"
    __table_args__ = (UniqueConstraint("workflow_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflow.id"))
    version: Mapped[int] = mapped_column(Integer)
    steps_revision: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workflow: Mapped[Workflow] = relationship(back_populates="script_versions")


class Run(Base):
    __tablename__ = "run"
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflow.id"))
    credential_id: Mapped[int] = mapped_column(ForeignKey("credential.id"))
    credential_label: Mapped[str] = mapped_column(String(120))
    script_version_id: Mapped[int | None] = mapped_column(ForeignKey("script_version.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED)
    active_slot: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    show_browser: Mapped[bool] = mapped_column(Boolean, default=False)
    force_generate: Mapped[bool] = mapped_column(Boolean, default=False)
    generated: Mapped[bool] = mapped_column(Boolean, default=False)
    used_session: Mapped[bool] = mapped_column(Boolean, default=False)
    trigger: Mapped[str] = mapped_column(String(40), default="operator")
    output: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    screenshot_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    inputs: Mapped[str | None] = mapped_column(Text, nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow: Mapped[Workflow] = relationship()
    script_version: Mapped[ScriptVersion | None] = relationship()

    @property
    def final_page_url(self) -> str | None:
        """The address of the page a successful script ended on (e.g. the published presentation), from its log."""
        return final_page_url_in(self.output) if self.status == RunStatus.SUCCEEDED else None

    @property
    def publication_status(self) -> str | None:
        """What the public check of the final page found, or None when it was not run (see `PublicationStatus`)."""
        if not self.final_page_url:
            return None
        matches = _PUBLICATION_LINE.findall(self.output or "")
        return matches[-1] if matches else None

    @property
    def input_values(self) -> dict[str, dict[str, str]]:
        """The Inputs this Run used, by name: `{"kind", "value"}` for text, `{"kind", "filename", "path"}` for uploads."""
        return json.loads(self.inputs) if self.inputs else {}
