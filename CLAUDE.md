# Agent Payment Firewall
Payment firewall between an AI shopping agent and PayPal (sandbox).
Stack: Python, FastAPI, PayPal REST API via requests (no SDK), Pydantic, SQLite, plain HTML/JS, Render.
Rules:
- Use Decimal for money, never float
- PayPal orders use AUTHORIZE intent; firewall decides capture or void
- Rule checks can block; LLM checks can only escalate to hold, never allow
- Never read, print, or commit .env or secrets
- Keep it simple: no extra frameworks or features unless asked
