"""The agent's tools: JSON descriptions the LLM reads, plus the Python functions that run them."""

import os

import requests
from dotenv import load_dotenv

from contracts.events import EventLog
from payment.client import MOCK_SIGNER, PaymentError, fetch_paid

load_dotenv()
SHOP_URL = os.getenv("SHOP_URL", "http://localhost:5001")

events = EventLog()  # shared with agent.py, served on GET /events

# Payment event type -> name of the step in the /run trace ("request" is left out).
TRACE_STEPS = {"payment_required": "402", "signed": "signed", "settled": "paid", "failed": "failed"}


class Recorder:
    """Passed to fetch_paid as its log: copies each event to the shared log and keeps it as a trace step."""

    def __init__(self):
        self.steps = []

    def append(self, event):
        events.append(event)
        if event.type in TRACE_STEPS:
            self.steps.append({"step": TRACE_STEPS[event.type], **event.data})


def list_paid_apis():
    """Read the shop's catalog: which paid endpoints exist and what each costs."""
    try:
        resp = requests.get(f"{SHOP_URL}/catalog", timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"shop unreachable at {SHOP_URL}: {e}"}
    return {"catalog": resp.json()}


def call_paid_api(url):
    """Pay for and fetch one endpoint. Returns data, a short payment summary, and the payment steps."""
    if url.startswith("/"):
        url = SHOP_URL + url
    recorder = Recorder()
    try:
        data, payment = fetch_paid(url, recorder)
    except PaymentError as e:
        return {"error": str(e), "steps": recorder.steps}
    summary = None
    if payment:
        simulated = MOCK_SIGNER or "mock" in str(payment["tx"]).lower()  # e.g. "0xmock…" receipts
        summary = {"amount_usd": payment["amount_usd"], "tx": payment["tx"], "simulated": simulated}
        recorder.steps[-1]["simulated"] = simulated  # the "paid" step
    return {"data": data, "payment": summary, "steps": recorder.steps}


# What the model sees: name, purpose and arguments of each tool.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_paid_apis",
            "description": "List the paid data APIs available, with their paths and USD prices.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_paid_api",
            "description": "Call one paid API (pays automatically) and return its data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "A catalog path plus ?symbol=BTC, ETH or SOL, e.g. /api/price?symbol=ETH",
                    }
                },
                "required": ["url"],
            },
        },
    },
]

# What the code runs: tool name -> Python function.
FUNCTIONS = {"list_paid_apis": list_paid_apis, "call_paid_api": call_paid_api}
