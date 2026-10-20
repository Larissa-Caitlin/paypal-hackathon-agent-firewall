from decimal import Decimal
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Agent Payment Firewall")


class PaymentProposal(BaseModel):
    user_instruction: str
    budget: Decimal = Field(gt=0)
    payee_email: str
    amount: Decimal = Field(gt=0)


class Verdict(BaseModel):
    decision: Literal["allow", "hold", "block"]
    reasons: list[str]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/check", response_model=Verdict)
def check(proposal: PaymentProposal) -> Verdict:
    if proposal.amount > proposal.budget:
        return Verdict(
            decision="block",
            reasons=[f"Amount {proposal.amount} exceeds budget {proposal.budget}"],
        )
    return Verdict(decision="allow", reasons=["Within budget"])
