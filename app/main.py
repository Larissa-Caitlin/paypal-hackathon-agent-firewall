from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.catalog import list_shops
from app.shop_pages import render_shop_page

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


@app.get("/shops")
def shops():
    return [{"shop_key": key, "shop_name": name} for key, name in list_shops()]


@app.get("/shops/{shop_key}", response_class=HTMLResponse)
def shop_page(shop_key: str):
    if shop_key not in dict(list_shops()):
        raise HTTPException(status_code=404, detail=f"Unknown shop: {shop_key}")
    return render_shop_page(shop_key)
