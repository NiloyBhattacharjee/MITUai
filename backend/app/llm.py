"""LLM behind one small interface: run(system, history, tools, call_tool, usage) -> reply text.

history: [{"role": "user"|"assistant", "text": str, "images": [{"mime": str, "data": bytes}]}], alternating, ending with the user.
Each provider runs its own tool loop in its native format (keeps thinking/tool blocks intact),
and appends one {model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens} per API call to usage.
"""
import base64
import json
import logging

from .config import LLM_PROVIDER, env

log = logging.getLogger(__name__)
MAX_STEPS = 8

# Claude API list prices, $ per million tokens: (input, output, cache read). Cache writes cost 1.25x input.
# Bedrock bills its own (similar) prices; Ollama runs locally for free.
PRICES = {"claude-fable-5-1": (10, 50, 1), "claude-opus-5-5": (4, 20, 0.2), "claude-opus-5": (5, 25, 0.5),
          "claude-opus-4-8": (5, 25, 0.5), "claude-sonnet-5-5": (2, 10, 0.2), "claude-sonnet-5": (2, 10, 0.2),
          "claude-haiku-4-5": (1, 5, 0.1)}


def cost_usd(u: dict) -> float | None:
    if u["model"].startswith("ollama:"):
        return 0.0
    name = max((n for n in PRICES if n in u["model"]), key=len, default=None)  # longest match: opus-5-5 before opus-5
    if not name:
        return None
    inp, out, read = PRICES[name]
    return (u["input_tokens"] * inp + u["output_tokens"] * out + u["cache_read_tokens"] * read
            + u["cache_write_tokens"] * inp * 1.25) / 1_000_000


def _b64(image: dict) -> str:
    return base64.b64encode(image["data"]).decode()


def _usage(model, inp=0, out=0, read=0, write=0) -> dict:
    return {"model": model, "input_tokens": inp or 0, "output_tokens": out or 0,
            "cache_read_tokens": read or 0, "cache_write_tokens": write or 0}


class BedrockLLM:
    def __init__(self):
        import boto3
        self.client = boto3.client("bedrock-runtime", region_name=env("AWS_REGION", "us-east-1"))
        self.model = env("BEDROCK_MODEL_ID", "anthropic.claude-opus-5-5")

    def run(self, system, history, tools, call_tool, usage=None):
        messages = [{"role": m["role"], "content": [
            *({"image": {"format": i["mime"].split("/")[1], "source": {"bytes": i["data"]}}} for i in m.get("images") or []),
            {"text": m["text"]}]} for m in history]
        tool_config = {"tools": [{"toolSpec": {"name": t["name"], "description": t["description"],
                                               "inputSchema": {"json": t["input_schema"]}}} for t in tools]}
        for _ in range(MAX_STEPS):
            r = self.client.converse(modelId=self.model, system=[{"text": system}], messages=messages,
                                     toolConfig=tool_config, inferenceConfig={"maxTokens": 4096})
            if usage is not None:
                usage.append(_usage(self.model, r["usage"]["inputTokens"], r["usage"]["outputTokens"]))
            msg = r["output"]["message"]
            messages.append(msg)
            uses = [b["toolUse"] for b in msg["content"] if "toolUse" in b]
            if r["stopReason"] != "tool_use" or not uses:
                return "".join(b.get("text", "") for b in msg["content"]).strip()
            messages.append({"role": "user", "content": [
                {"toolResult": {"toolUseId": u["toolUseId"], "content": [{"text": json.dumps(call_tool(u["name"], u["input"]), ensure_ascii=False)}]}}
                for u in uses]})
        return ""


class AnthropicLLM:
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = env("ANTHROPIC_MODEL", "claude-opus-5-5")
        self.effort = env("LLM_EFFORT", "low")  # short chat replies; raise if quality needs it

    def run(self, system, history, tools, call_tool, usage=None):
        messages = [{"role": m["role"], "content": [
            *({"type": "image", "source": {"type": "base64", "media_type": i["mime"], "data": _b64(i)}} for i in m.get("images") or []),
            {"type": "text", "text": m["text"]}]} for m in history]
        for _ in range(MAX_STEPS):
            r = self.client.beta.messages.create(
                model=self.model, max_tokens=16000, system=system, messages=messages, tools=tools,
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},  # re-sent tools+system+history bill at 0.1x within 5 min
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",  # retry safety-classifier refusals on another model
            )
            if usage is not None:  # r.model: a refusal fallback may have answered with another model
                usage.append(_usage(r.model, r.usage.input_tokens, r.usage.output_tokens,
                                    r.usage.cache_read_input_tokens, r.usage.cache_creation_input_tokens))
            if r.stop_reason == "refusal":
                log.warning("LLM refusal: %s", r.stop_details)
                return ""
            messages.append({"role": "assistant", "content": r.content})
            uses = [b for b in r.content if b.type == "tool_use"]
            if r.stop_reason != "tool_use" or not uses:
                return "".join(b.text for b in r.content if b.type == "text").strip()
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(call_tool(u.name, u.input), ensure_ascii=False)}
                for u in uses]})
        return ""


class OllamaLLM:
    """Local model via Ollama's native chat API. Needs a model with tool support (qwen3, qwen2.5, llama3.1+)."""

    def __init__(self):
        import httpx
        self.http = httpx.Client(base_url=env("OLLAMA_URL", "http://localhost:11434"), timeout=600)  # CPU inference is slow
        self.model = env("OLLAMA_MODEL", "qwen3:4b")
        self.options = {"num_ctx": int(env("OLLAMA_NUM_CTX", "8192"))}  # Ollama's default (64k) eats ~10 GB of RAM

    def run(self, system, history, tools, call_tool, usage=None):
        # /no_think: qwen3's switch; "think": False alone doesn't stop it reasoning after tool results
        # ponytail: images need a vision model (e.g. qwen2.5vl, gemma3); a text-only model errors and the chat goes to a human.
        messages = [{"role": "system", "content": system + "\n/no_think"}] + [
            {"role": m["role"], "content": m["text"], **({"images": [_b64(i) for i in m["images"]]} if m.get("images") else {})}
            for m in history]
        defs = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
                for t in tools]
        for _ in range(MAX_STEPS):
            r = self.http.post("/api/chat", json={"model": self.model, "messages": messages, "tools": defs, "stream": False,
                                                 "think": False, "options": self.options})  # thinking makes CPU replies minutes slower
            if r.is_error:
                raise RuntimeError(f"Ollama {r.status_code}: {r.text}")
            data = r.json()
            msg = data["message"]
            if usage is not None:
                usage.append(_usage(f"ollama:{self.model}", data.get("prompt_eval_count"), data.get("eval_count")))
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                return msg.get("content", "").split("</think>")[-1].strip()  # drop any leaked reasoning
            for c in calls:
                f = c["function"]
                messages.append({"role": "tool", "tool_name": f["name"],
                                 "content": json.dumps(call_tool(f["name"], f.get("arguments") or {}), ensure_ascii=False)})
        return ""


_llm = None


def get_llm():
    global _llm
    if _llm is None:
        _llm = {"anthropic": AnthropicLLM, "ollama": OllamaLLM}.get(LLM_PROVIDER, BedrockLLM)()
    return _llm
