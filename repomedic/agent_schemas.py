from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_INVESTIGATION_SEARCHES = 6
MAX_INVESTIGATION_READS = 8
MAX_RELEVANT_FILES = 12


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlanReport(StrictModel):
    acceptance_criteria: tuple[str, ...] = Field(min_length=1, max_length=8)
    investigation_tasks: tuple[str, ...] = Field(min_length=1, max_length=8)
    candidate_paths: tuple[str, ...] = Field(min_length=1, max_length=12)
    repair_steps: tuple[str, ...] = Field(min_length=1, max_length=8)


class InvestigationRequest(StrictModel):
    searches: tuple[str, ...] = Field(max_length=MAX_INVESTIGATION_SEARCHES)
    reads: tuple[str, ...] = Field(max_length=MAX_INVESTIGATION_READS)

    @model_validator(mode="after")
    def require_an_operation(self) -> "InvestigationRequest":
        if not self.searches and not self.reads:
            raise ValueError("investigation must request at least one search or read")
        return self


class Evidence(StrictModel):
    path: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_line_range(self) -> "Evidence":
        if self.line_end < self.line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        return self


class InvestigationReport(StrictModel):
    root_cause: str = Field(min_length=1, max_length=4000)
    evidence: tuple[Evidence, ...] = Field(min_length=1, max_length=12)
    relevant_files: tuple[str, ...] = Field(
        min_length=1, max_length=MAX_RELEVANT_FILES
    )


class TextReplacement(StrictModel):
    path: str = Field(min_length=1)
    old: str = Field(min_length=1, max_length=20000)
    new: str = Field(max_length=20000)
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_a_change(self) -> "TextReplacement":
        if self.old == self.new:
            raise ValueError("old and new text must differ")
        return self


class CodeProposal(StrictModel):
    summary: str = Field(min_length=1, max_length=2000)
    edits: tuple[TextReplacement, ...] = Field(min_length=1, max_length=8)


class ReviewReport(StrictModel):
    verdict: Literal["pass", "revise", "replan", "stop"]
    reasons: tuple[str, ...] = Field(min_length=1, max_length=8)
    feedback: str = Field(default="", max_length=4000)
    requested_paths: tuple[str, ...] = Field(default=(), max_length=8)


class ApprovalDecision(StrictModel):
    action: Literal["approve", "revise", "reject"]
    feedback: str = Field(default="", max_length=4000)
