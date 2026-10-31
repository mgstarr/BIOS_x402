"""Step 1 check: is Ollama reachable, and how fast does the model make one tool call?"""

import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# A made-up tool, described in the JSON format Ollama expects. We never run it.
PRICE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_price",
        "description": "Get the latest USD price of a crypto asset.",
        "parameters": {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "e.g. ETH"}},
            "required": ["symbol"],
        },
    },
}


def check_reachable():
    """Ask Ollama for its model list; stop with a clear message if anything is wrong."""
    try:
        tags = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()
    except requests.RequestException:
        sys.exit(f"FAIL: Ollama not reachable at {OLLAMA_URL}. Run: ollama serve")
    names = [m["name"] for m in tags["models"]]
    if OLLAMA_MODEL not in names:
        sys.exit(f"FAIL: model {OLLAMA_MODEL} not found. Run: ollama pull {OLLAMA_MODEL}")
    print(f"OK: Ollama reachable at {OLLAMA_URL}, model {OLLAMA_MODEL} present")


def timed_tool_call(label):
    """Send one chat request with the tool attached; print time and what the model chose."""
    body = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": "What is the price of ETH?"}],
        "tools": [PRICE_TOOL],
        "stream": False,
    }
    start = time.perf_counter()
    reply = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=300).json()
    seconds = time.perf_counter() - start

    calls = reply["message"].get("tool_calls", [])
    if calls:
        fn = calls[0]["function"]
        print(f"{label}: {seconds:.1f}s, model called {fn['name']}({fn['arguments']})")
    else:
        print(f"{label}: {seconds:.1f}s, model did NOT call the tool: {reply['message']['content']!r}")
    return seconds, bool(calls)


if __name__ == "__main__":
    check_reachable()
    timed_tool_call("cold (loads model)")
    warm, called = timed_tool_call("warm")
    if not called:
        print("WARN: no tool call; we may need the JSON 'router' fallback")
    elif warm > 20:
        print("SLOW: over 20s; set OLLAMA_MODEL=qwen2.5:3b or llama3.2:3b in .env")
    else:
        print("VERDICT: OK, fast enough for the agent")
