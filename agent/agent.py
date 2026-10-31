"""Agent core: runs a task under a Mandate, deciding when and how much to pay via x402."""

import json

import requests
from flask import Flask, jsonify, request
from pydantic import ValidationError

from agent.client import FUNCTIONS, TOOLS, events
from agent.llm import chat
from contracts.events import Event
from contracts.mandate import Mandate
from payment.client import MOCK_SIGNER, SIGNER_URL

MAX_STEPS = 6
SYSTEM_PROMPT = (
    "You are a helpful agent. First call list_paid_apis to see which data APIs exist and their prices. "
    "Then use call_paid_api only for the endpoints the task needs, because each call costs money. "
    "Never invent numbers. When you have the data, answer the task briefly. "
    "If a tool returns an error, do not retry it: explain the error in your answer."
)
# The only parts of a tool result the model may see: no bills, signatures or payment steps.
LLM_FIELDS = ("data", "payment", "error", "catalog")

app = Flask(__name__)


def run_tool(name, args):
    """Run one tool the model asked for; return its result (or an error the model can read)."""
    if name not in FUNCTIONS:
        return {"error": f"unknown tool {name}"}
    try:
        return FUNCTIONS[name](**args)
    except Exception as e:
        return {"error": str(e)}


def run_agent(task, mandate):
    """The agent loop: ask the model, run any tools it picks, feed results back, until it answers."""
    trace = [{"step": "mandate", "mandate": mandate.model_dump() if mandate else None}]
    payments = []
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    for _ in range(MAX_STEPS):
        reply = chat(messages, TOOLS)
        messages.append(reply)
        calls = reply.get("tool_calls") or []

        if not calls:  # plain text = the final answer
            trace.append({"step": "answer", "text": reply["content"]})
            return {"answer": reply["content"], "payments": payments, "trace": trace}

        for call in calls:
            name, args = call["function"]["name"], call["function"]["arguments"]
            thought = reply.get("content", "").strip() or f"decided to call {name}({', '.join(map(str, args.values()))})"
            trace.append({"step": "thought", "text": thought})
            trace.append({"step": "tool", "name": name, "args": args})

            result = run_tool(name, args)
            trace.extend(result.get("steps", []))  # 402 -> signed -> paid (or failed)
            for_llm = {k: v for k, v in result.items() if k in LLM_FIELDS}
            trace.append({"step": "result", **for_llm})
            if result.get("payment"):
                payments.append({"url": args.get("url"), **result["payment"]})
            messages.append({"role": "tool", "tool_name": name, "content": json.dumps(for_llm)})

    trace.append({"step": "answer", "text": None, "error": "max steps reached"})
    return {"answer": None, "payments": payments, "trace": trace}


@app.post("/run")
def run():
    body = request.get_json(silent=True) or {}
    task = body.get("task")
    if not task:
        return jsonify({"error": "missing 'task'"}), 400
    try:
        mandate = Mandate(**body["mandate"]) if body.get("mandate") else None
    except (ValidationError, TypeError) as e:
        return jsonify({"error": f"bad mandate: {e}"}), 400

    events.append(Event(service="agent", type="request", data={"task": task}))
    try:
        result = run_agent(task, mandate)
    except RuntimeError as e:  # e.g. Ollama down
        events.append(Event(service="agent", type="failed", data={"error": str(e)}))
        return jsonify({"error": str(e)}), 502
    events.append(Event(service="agent", type="answer", data={"answer": result["answer"]}))
    return jsonify(result)


@app.get("/events")
def get_events():
    return jsonify([e.model_dump() for e in events.last(100)])


def payment_mode():
    """One line saying whether payments are simulated or real, so a demo is never mislabelled."""
    if MOCK_SIGNER:
        return "Payments: SIMULATED (MOCK_SIGNER=true)"
    try:
        address = requests.get(f"{SIGNER_URL}/address", timeout=5).json()["address"]
        return f"Payments: real signer at {SIGNER_URL}, wallet {address}"
    except Exception:
        return f"Payments: real signer at {SIGNER_URL} -- NOT REACHABLE yet"


if __name__ == "__main__":
    print(payment_mode())
    app.run(host="0.0.0.0", port=7000)
