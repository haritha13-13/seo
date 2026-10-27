"""The workflow spec: one YAML file per portal workflow."""
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Workflow(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    url: str
    headless: bool = True
    steps: list[str] = Field(min_length=1)
    allowed_hosts: list[str] = Field(default_factory=list)

    @property
    def numbered_steps(self) -> str:
        return "\n".join(f"{n}. {step}" for n, step in enumerate(self.steps, 1))


def load_workflow(path: Path) -> Workflow:
    return Workflow.model_validate(yaml.safe_load(Path(path).read_text()))
