"""The agent's tools: JSON descriptions the LLM reads, plus the Python functions that run them."""

import os

import requests
from dotenv import load_dotenv

from contracts.events import EventLog
from payment.client import MOCK_SIGNER, PaymentBlocked, PaymentError, fetch_paid

load_dotenv()
SHOP_URL = os.getenv("SHOP_URL", "http://localhost:5001")

events = EventLog()  # shared with agent.py, served on GET /events

# Payment event type -> name of the step in the /run trace ("request" is left out).
TRACE_STEPS = {"request": "request", "payment_required": "402", "signed": "signed", "settled": "paid",
               "blocked": "blocked", "failed": "failed"}


def is_simulated(tx):
    """A mock receipt (e.g. "0xmock…") or mock signer means no money really moved."""
    return MOCK_SIGNER or "mock" in str(tx).lower()


class Recorder:
    """Passed to fetch_paid as its log: copies each event to the shared log and keeps it as a trace step.

    on_step, if given, is called with each step the moment it happens (the live view of a run).
    """

    def __init__(self, on_step=None):
        self.steps = []
        self.on_step = on_step

    def append(self, event):
        events.append(event)
        if event.type in TRACE_STEPS:
            step = {"step": TRACE_STEPS[event.type], "ts": event.ts, **event.data}
            if event.type == "settled":
                step["simulated"] = is_simulated(event.data["tx"])
            self.steps.append(step)
            if self.on_step:
                self.on_step(step)


def list_paid_apis():
    """Read the shop's catalog: which paid endpoints exist and what each costs."""
    try:
        resp = requests.get(f"{SHOP_URL}/catalog", timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"shop unreachable at {SHOP_URL}: {e}"}
    return {"catalog": resp.json()}


def call_paid_api(url, context=None, on_step=None):
    """Pay for and fetch one URL. context = {"task", "mandate"}, passed on to the defender.

    Returns the data, the last payment (for the LLM), every payment as
    {url, amount_usd, payTo, status: paid|blocked, reason, tx}, and the payment steps.
    """
    if url.startswith("/"):
        url = SHOP_URL + url
    recorder, paid = Recorder(on_step), []
    result = {"steps": recorder.steps, "payments": []}
    blocked = None
    try:
        result["data"] = fetch_paid(url, recorder, paid, context)
    except PaymentBlocked as e:
        result["error"] = str(e)  # the LLM reads "payment blocked by the guard: <reason>"
        blocked = e
    except PaymentError as e:
        result["error"] = str(e)

    for p in paid:  # payments that went through (a dishonest shop may take two)
        simulated = is_simulated(p["tx"])
        result["payments"].append({"url": url, "amount_usd": p["amount_usd"], "payTo": p["payTo"],
                                   "status": "paid", "reason": None, "tx": p["tx"], "simulated": simulated})
        result["payment"] = {"amount_usd": p["amount_usd"], "tx": p["tx"], "simulated": simulated}
    if blocked:
        bill = [s for s in recorder.steps if s["step"] == "402"][-1]  # the bill the guard refused
        result["payments"].append({"url": url, "amount_usd": bill["amount_usd"], "payTo": bill["payTo"],
                                   "status": "blocked", "check": blocked.check, "reason": blocked.reason,
                                   "tx": None})
    return result


# What the model sees: name, purpose and arguments of each tool.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_paid_apis",
            "description": "List the paid data APIs available (crypto, weather, shares, currencies, news), "
                           "with their paths, parameters and USD prices.",
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
                        "description": (
                            "A full URL given in the task, or a catalog path plus its parameter, "
                            "e.g. /api/price?symbol=ETH or /api/weather?city=Tokyo (see each catalog item's "
                            "param and options)"
                        ),
                    }
                },
                "required": ["url"],
            },
        },
    },
]

# What the code runs: tool name -> Python function.
FUNCTIONS = {"list_paid_apis": list_paid_apis, "call_paid_api": call_paid_api}
