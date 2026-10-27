"""Output contract for the agent. The final answer must validate against IncidentAssessment."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.kubectl import ALLOWED_VERBS


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceRef(_Strict):
    kind: str = Field(description="Kubernetes kind, e.g. Pod, Deployment, Node.")
    name: str
    namespace: str | None = Field(default=None, description="Omit for cluster-scoped kinds.")


class Hypothesis(_Strict):
    cause: str = Field(description="Candidate root cause.")
    confidence: float = Field(ge=0, le=1, description="Probability estimate between 0 and 1.")
    evidence: list[str] = Field(description="Observations supporting or weakening this cause.")
    diagnostic_commands: list[str] = Field(
        description=(
            "Read-only commands that would confirm or rule out this cause. "
            "kubectl commands must put the verb first and use only get/describe/logs/top."
        )
    )

    @field_validator("diagnostic_commands")
    @classmethod
    def _kubectl_read_only(cls, commands: list[str]) -> list[str]:
        for command in commands:
            tokens = command.split()
            if tokens and tokens[0] == "kubectl":
                verb = tokens[1] if len(tokens) > 1 else ""
                if verb not in ALLOWED_VERBS:
                    raise ValueError(
                        f"diagnostic command {command!r} must start with 'kubectl <verb>' "
                        f"where verb is one of {sorted(ALLOWED_VERBS)}"
                    )
        return commands


class IncidentAssessment(_Strict):
    summary: str = Field(description="One-paragraph description of the incident.")
    severity: Literal["SEV1", "SEV2", "SEV3", "SEV4"] = Field(description="SEV1 is most severe.")
    affected_resources: list[ResourceRef]
    verified_facts: list[str] = Field(description="Facts directly observed in tool output.")
    assumptions: list[str] = Field(description="Things taken as true without verification.")
    hypotheses: list[Hypothesis] = Field(
        description="Ranked most likely first. Re-sorted by descending confidence on validation."
    )
    safe_next_actions: list[str] = Field(
        description="Low-risk actions an operator can take next, most important first."
    )
    rollback_plan: str | None = Field(
        default=None, description="How to roll back if a recent change is implicated."
    )

    @model_validator(mode="after")
    def _rank_hypotheses(self) -> IncidentAssessment:
        # Stable sort: hypotheses with equal confidence keep the model's order.
        self.hypotheses = sorted(self.hypotheses, key=lambda h: h.confidence, reverse=True)
        return self
