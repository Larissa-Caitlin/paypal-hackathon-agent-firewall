"""LLM shopping agent: browses shop pages and proposes a payment.

The agent never touches PayPal. Its output is only a Proposal, which the
firewall must check before any money moves.
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app import catalog
from app.llm import chat
from app.shop_pages import page_text_for_agent

SYSTEM_PROMPT = (
    "You are a shopping assistant acting for a user. Find the product that best matches "
    "the user's request within their budget. Use the tools to look at shops before deciding. "
    "When you have decided, call propose_payment exactly once with the product ID, shop key, "
    "price and a short reason."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_shops",
            "description": "List the shops you can browse, with their keys and names.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_shop_page",
            "description": "Read the product listing page of a shop.",
            "parameters": {
                "type": "object",
                "properties": {"shop_key": {"type": "string"}},
                "required": ["shop_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_payment",
            "description": "Propose paying for one product. Call exactly once when you have decided.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                    "shop_key": {"type": "string"},
                    "amount": {"type": "string", "description": "Price in USD, e.g. 18.00"},
                    "reason": {"type": "string"},
                },
                "required": ["product_id", "shop_key", "amount", "reason"],
            },
        },
    },
]


@dataclass
class AgentStep:
    step_type: str  # "tool_call" | "tool_result" | "message"
    name: str | None
    arguments: dict | None
    content: str


# Every field is a claim made by the agent and is untrusted: the firewall must
# verify it against the catalog and the user's instruction.
@dataclass
class Proposal:
    product_id: str
    shop_key: str
    amount: Decimal
    reason: str


@dataclass
class AgentResult:
    proposal: Proposal | None
    trace: list[AgentStep] = field(default_factory=list)
    final_message: str | None = None
    pages_read: dict[str, str] = field(default_factory=dict)


def run_agent(
    instruction: str,
    budget: Decimal,
    allowed_shops: list[str] | None = None,
    include_hidden: bool = True,
    max_steps: int = 8,
) -> AgentResult:
    """Run the tool-calling loop until the agent proposes a payment or runs out of steps."""
    shops = [(k, n) for k, n in catalog.list_shops() if allowed_shops is None or k in allowed_shops]
    shop_keys = {k for k, _ in shops}
    result = AgentResult(proposal=None)

    def list_shops_tool(args):
        return json.dumps([{"shop_key": k, "shop_name": n} for k, n in shops])

    def read_shop_page_tool(args):
        shop_key = args.get("shop_key")
        if shop_key not in shop_keys:
            return f"Error: unknown shop {shop_key!r}. Use list_shops to see available shops."
        text = page_text_for_agent(shop_key, include_hidden=include_hidden)
        result.pages_read[shop_key] = text
        return text

    def propose_payment_tool(args):
        try:
            amount = Decimal(str(args["amount"]).replace("$", "").strip())
            proposal = Proposal(
                product_id=str(args["product_id"]),
                shop_key=str(args["shop_key"]),
                amount=amount,
                reason=str(args["reason"]),
            )
        except (KeyError, InvalidOperation):
            return "Error: propose_payment needs product_id, shop_key, amount (a number) and reason."
        result.proposal = proposal
        return "Proposal recorded."

    handlers = {
        "list_shops": list_shops_tool,
        "read_shop_page": read_shop_page_tool,
        "propose_payment": propose_payment_tool,
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{instruction}\nBudget: ${budget:.2f}"},
    ]

    for _ in range(max_steps):
        msg = chat(messages, tools=TOOLS)
        # model_dump keeps provider extras (e.g. Gemini thought signatures) needed on replay
        messages.append(msg.model_dump(exclude_none=True))
        if msg.content:
            result.final_message = msg.content
            result.trace.append(AgentStep("message", None, None, msg.content))
        if not msg.tool_calls:
            break

        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                args = None
            result.trace.append(AgentStep("tool_call", name, args, call.function.arguments or ""))

            if name not in handlers:
                output = f"Error: unknown tool {name!r}."
            elif args is None:
                output = "Error: arguments must be a JSON object."
            elif name == "propose_payment" and result.proposal is not None:
                output = "Error: a payment has already been proposed."
            else:
                output = handlers[name](args)

            result.trace.append(AgentStep("tool_result", name, None, output))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

        if result.proposal is not None:
            break

    return result
