"""Firewall decision engine: combines rule findings and AI findings into a verdict."""

from dataclasses import dataclass
from decimal import Decimal

from app.agent import Proposal
from app.catalog import get_product
from app.firewall_ai import AIFinding, run_ai_checks
from app.firewall_rules import Finding, check_rules
from app.paypal_client import OrderInfo


@dataclass
class Verdict:
    decision: str  # "allow" | "hold" | "block"
    reasons: list[str]
    rule_findings: list[Finding]
    ai_findings: list[AIFinding]


def decide(rule_findings: list[Finding], ai_findings: list[AIFinding]) -> Verdict:
    """Block on any failed rule; otherwise hold on any flagged AI check; otherwise allow."""
    failed_rules = [f.reason for f in rule_findings if not f.passed]
    flagged_ai = [f.reason for f in ai_findings if f.flagged]
    # AI checks can only raise the decision, never lower it. They are
    # probabilistic and read attacker-influenced text, so a "looks fine" from
    # an LLM must never override a deterministic rule failure, and an LLM
    # flag can at most send a payment to a human (hold), never block alone.
    if failed_rules:
        return Verdict("block", failed_rules + flagged_ai, rule_findings, ai_findings)
    if flagged_ai:
        return Verdict("hold", flagged_ai, rule_findings, ai_findings)
    return Verdict("allow", ["All checks passed"], rule_findings, ai_findings)


def evaluate(
    instruction: str, budget: Decimal, proposal: Proposal, order: OrderInfo, pages_read: dict
) -> Verdict:
    """Run all rule and AI checks (always both, for a complete audit trail) and decide."""
    rule_findings = check_rules(budget, proposal, order)
    ai_findings = run_ai_checks(instruction, get_product(proposal.product_id), pages_read)
    return decide(rule_findings, ai_findings)
