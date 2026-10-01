from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .llm import ModelAdapter


@dataclass(frozen=True)
class AgentIdentity:
    key: str
    name: str
    responsibility: str


ANALYST = AgentIdentity("problem_analyst", "Problem Analyst", "理解问题并形成结构化任务说明")
SOLVER = AgentIdentity("scientific_solver", "Scientific Solver", "规划并调用受控科学计算工具")
CRITIC = AgentIdentity("verification_critic", "Verification Critic", "独立检查计算证据并决定是否退回")
REPORTER = AgentIdentity("report_writer", "Report Writer", "仅根据通过验证的证据生成最终答复")
AGENTS = {agent.key: agent for agent in (ANALYST, SOLVER, CRITIC, REPORTER)}


class ProblemAnalystAgent:
    identity = ANALYST

    async def analyze(self, model: ModelAdapter, query: str, fallback: dict[str, Any]):
        return await model.analyze(query, fallback)


class ScientificSolverAgent:
    identity = SOLVER

    async def decide(self, model: ModelAdapter, query: str, parsed: dict[str, Any],
                     history: list[dict[str, Any]], suggested: dict[str, Any]):
        return await model.decide(query, parsed, history, suggested)


class VerificationCriticAgent:
    identity = CRITIC

    async def verify(self, model: ModelAdapter, query: str, call: dict[str, Any], tolerance: float,
                     verifier: Callable[[dict[str, Any], float], dict[str, Any]]):
        evidence = verifier(call, tolerance)
        review, record = await model.review_verification(query, call, evidence)
        # Keep objective evidence separate from the model's local-step review.
        # Global task completion is decided later by the solver's goal ledger.
        program_passed = bool(evidence["passed"])
        agent_approved = bool(review.get("approved", False))
        evidence["program_passed"] = program_passed
        evidence["agent_approved"] = agent_approved
        evidence["passed"] = program_passed and agent_approved
        evidence["agent_review"] = review
        return evidence, record


class ReportWriterAgent:
    identity = REPORTER

    async def write(self, model: ModelAdapter, query: str, history: list[dict[str, Any]], reason: str):
        return await model.synthesize(query, history, reason)


problem_analyst = ProblemAnalystAgent()
scientific_solver = ScientificSolverAgent()
verification_critic = VerificationCriticAgent()
report_writer = ReportWriterAgent()


def handoff_record(source: AgentIdentity, target: AgentIdentity, reason: str) -> dict[str, str]:
    return {"from": source.key, "to": target.key, "reason": reason}
