from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from portal_auto.db.models import Run, RunStatus, ScriptVersion, Workflow, WorkflowStep
from portal_auto.inputs import InputSpec, parse_inputs
from portal_auto.services._common import commit_unique, required
from portal_auto.services.errors import InUse, Invalid, NotFound
from portal_auto.services.portals import PortalService


@dataclass(frozen=True)
class WorkflowUsage:
    runs: int
    script_versions: int
    active_runs: int


class WorkflowService:
    def __init__(self, session: Session):
        self._session = session

    def create(self, portal_id: int, name: str, steps: list[str], starts_signed_in: bool = False) -> Workflow:
        PortalService(self._session).get(portal_id)
        workflow = Workflow(
            portal_id=portal_id, name=required(name, "Workflow name"), starts_signed_in=starts_signed_in
        )
        workflow.steps = _to_steps(_clean_steps(steps))
        self._session.add(workflow)
        return self._commit(workflow)

    def update(self, workflow_id: int, name: str) -> Workflow:
        workflow = self.get(workflow_id)
        workflow.name = required(name, "Workflow name")
        return self._commit(workflow)

    def replace_steps(self, workflow_id: int, steps: list[str]) -> bool:
        workflow = self.get(workflow_id)
        instructions = _clean_steps(steps)
        if instructions == _instructions(workflow):
            return False
        workflow.steps.clear()
        self._session.flush()
        workflow.steps = _to_steps(instructions)
        workflow.steps_revision += 1
        self._commit(workflow)
        return True

    def set_starts_signed_in(self, workflow_id: int, starts_signed_in: bool) -> Workflow:
        workflow = self.get(workflow_id)
        if workflow.starts_signed_in != starts_signed_in:
            workflow.starts_signed_in = starts_signed_in
            workflow.steps_revision += 1  # the Steps now mean something different, so old scripts are Stale
        return self._commit(workflow)

    def set_active(self, workflow_id: int, active: bool) -> Workflow:
        workflow = self.get(workflow_id)
        workflow.active = active
        return self._commit(workflow)

    def get(self, workflow_id: int) -> Workflow:
        workflow = self._session.get(Workflow, workflow_id)
        if workflow is None:
            raise NotFound("Workflow not found")
        return workflow

    def inputs(self, workflow_id: int) -> list[InputSpec]:
        """The Inputs the Workflow's current Steps declare, in order of first appearance."""
        return parse_inputs(_instructions(self.get(workflow_id)))

    def list(self, portal_id: int | None = None, active_only: bool = False) -> list[Workflow]:
        query = select(Workflow).order_by(Workflow.name)
        if portal_id is not None:
            query = query.where(Workflow.portal_id == portal_id)
        if active_only:
            query = query.where(Workflow.active)
        return list(self._session.scalars(query))

    def current_script(self, workflow_id: int) -> ScriptVersion | None:
        workflow = self.get(workflow_id)
        self._session.refresh(workflow, ["script_versions"])
        return workflow.current_script_version

    def has_verify_step(self, workflow_id: int) -> bool:
        return any("verify" in text.lower() for text in _instructions(self.get(workflow_id)))

    def usage(self, workflow_id: int) -> "WorkflowUsage":
        """How much history a Workflow has: what deleting it would take with it."""
        self.get(workflow_id)
        runs = select(Run).where(Run.workflow_id == workflow_id)
        count = lambda query: self._session.scalar(select(func.count()).select_from(query.subquery()))
        return WorkflowUsage(
            runs=count(runs),
            script_versions=count(select(ScriptVersion).where(ScriptVersion.workflow_id == workflow_id)),
            active_runs=count(runs.where(Run.status.in_(RunStatus.ACTIVE))),
        )

    def delete(self, workflow_id: int, with_history: bool = False) -> None:
        """Without `with_history` a Workflow that has Runs or scripts is kept (deactivate it instead); with it,
        those Runs and scripts are deleted along with it, unless a Run is still in progress."""
        workflow = self.get(workflow_id)
        usage = self.usage(workflow_id)
        if (usage.runs or usage.script_versions) and not with_history:
            raise InUse("Workflow has script versions or Runs; deactivate it instead")
        if usage.active_runs:
            raise Invalid("A Run of this Workflow is in progress; wait for it to finish before deleting the Workflow")
        self._session.execute(delete(Run).where(Run.workflow_id == workflow_id))
        self._session.execute(delete(ScriptVersion).where(ScriptVersion.workflow_id == workflow_id))
        self._session.expire(workflow)
        self._session.delete(workflow)
        self._session.commit()

    def _commit(self, workflow: Workflow) -> Workflow:
        return commit_unique(self._session, workflow, "This portal already has a workflow with this name")


def _clean_steps(steps: list[str]) -> list[str]:
    cleaned = [step.strip() for step in steps if step.strip()]
    if not cleaned:
        raise Invalid("A workflow needs at least one step")
    parse_inputs(cleaned)
    return cleaned


def _to_steps(instructions: list[str]) -> list[WorkflowStep]:
    return [WorkflowStep(position=i, instruction=text) for i, text in enumerate(instructions, start=1)]


def _instructions(workflow: Workflow) -> list[str]:
    return [step.instruction for step in workflow.steps]
