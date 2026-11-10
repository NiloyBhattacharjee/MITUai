"""Run the eval on several models and compare pass rate, cost and speed.

    python compare_models.py anthropic:claude-sonnet-5-5 anthropic:claude-haiku-4-5 ollama:qwen3:4b
    python compare_models.py anthropic:claude-sonnet-5-5 ollama:qwen3:4b booking    only cases whose name has a word

provider:model sets LLM_PROVIDER and that provider's model; other arguments pick cases, as in eval.py. Each paid model
costs what one eval.py run costs on it. The table is printed and saved in evals/results/.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

MODEL_VAR = {"anthropic": "ANTHROPIC_MODEL", "bedrock": "BEDROCK_MODEL_ID", "ollama": "OLLAMA_MODEL"}
HERE = Path(__file__).parent


def env_for(spec: str) -> dict:
    """"ollama:qwen3:4b" -> {"LLM_PROVIDER": "ollama", "OLLAMA_MODEL": "qwen3:4b"}"""
    provider, _, model = spec.partition(":")
    if provider not in MODEL_VAR or not model:
        raise SystemExit(f"{spec}: write it as provider:model, with provider one of {list(MODEL_VAR)}")
    return {"LLM_PROVIDER": provider, MODEL_VAR[provider]: model}


def run(spec: str, words: list[str]) -> list[dict]:
    """eval.py on one model (its usual output streams by); returns its per-case results."""
    print(f"\n=== {spec}", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "results.json"
        subprocess.run([sys.executable, str(HERE / "eval.py"), *words], env={**os.environ, **env_for(spec), "EVAL_RESULTS": str(out)})
        return json.loads(out.read_text(encoding="utf-8")) if out.exists() else []


def table(runs: dict[str, list[dict]]) -> str:
    """A summary row per model, then every case that failed on at least one of them."""
    lines = ["| Model | Passed | Cost | $/case | s/turn |", "|---|---|---|---|---|"]
    for spec, rs in runs.items():
        cost = sum(r["cost_usd"] for r in rs)
        per_turn = sum(r["seconds"] for r in rs) / max(1, sum(r["turns"] for r in rs))
        lines.append(f"| {spec} | {sum(r['passed'] for r in rs)}/{len(rs)} | ${cost:.2f} | ${cost / max(1, len(rs)):.3f} | {per_turn:.1f} |")
    failed = sorted({r["name"] for rs in runs.values() for r in rs if not r["passed"]})
    if failed:
        lines += ["", "| Failed case | " + " | ".join(runs) + " |", "|---" * (len(runs) + 1) + "|"]
        for name in failed:
            marks = [next(("pass" if r["passed"] else "**fail**" for r in rs if r["name"] == name), "-") for rs in runs.values()]
            lines.append(f"| {name} | " + " | ".join(marks) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    specs = [a for a in sys.argv[1:] if ":" in a]
    words = [a for a in sys.argv[1:] if ":" not in a]
    if not specs:
        raise SystemExit(__doc__)
    for s in specs:
        env_for(s)  # a typo fails here, before any model is paid for
    runs = {s: run(s, words) for s in specs}
    report = table(runs)
    print("\n" + report)
    path = HERE / "evals" / "results" / f"{datetime.now():%Y-%m-%d-%H%M}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"# {' vs '.join(specs)}{' (' + ' '.join(words) + ')' if words else ''}\n\n{report}\n", encoding="utf-8")
    print(f"\nSaved to {path.relative_to(HERE)}")
