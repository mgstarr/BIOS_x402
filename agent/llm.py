"""LLM wrapper: talks to the local Ollama model to drive the agent's reasoning."""

import os

import requests
from dotenv import load_dotenv

load_dotenv()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")


def chat(messages, tools):
    """Send the conversation + tool descriptions to Ollama; return the model's reply message.

    The reply is a dict like {"role": "assistant", "content": "...", "tool_calls": [...]}.
    """
    body = {"model": OLLAMA_MODEL, "messages": messages, "tools": tools, "stream": False}
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Ollama call failed: {e}") from e
    return resp.json()["message"]
