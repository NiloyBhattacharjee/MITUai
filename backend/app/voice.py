"""Speech-to-text (OpenAI gpt-4o-transcribe, or Whisper), plus ffmpeg helpers."""
import io
import math
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import env
from .tenancy import cfg


@dataclass
class Transcript:
    text: str
    language: str | None
    confidence: float | None  # 0..1, None if the provider gives none


# --- ffmpeg -------------------------------------------------------------------------------

def _ffmpeg(data: bytes, in_ext: str, args: list[str], out_ext: str) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / f"in.{in_ext}", Path(d) / f"out.{out_ext}"
        src.write_bytes(data)  # temp files, not pipes: mp4/webm from browsers need seekable input
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), *args, str(dst)], check=True)
        return dst.read_bytes()


def to_wav(data: bytes, ext: str) -> bytes:
    return _ffmpeg(data, ext, ["-ac", "1", "-ar", "16000"], "wav")


def wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / w.getframerate()


# --- speech-to-text ------------------------------------------------------------------------

OPENAI_URL = "https://api.openai.com/v1/audio"


def stt_hint() -> str:
    """Whisper spells menu words better when it has seen them."""
    from .db import MenuItem, Session  # avoid import cycle
    from sqlalchemy import select
    with Session() as db:
        names = db.scalars(select(MenuItem.name)).all()
    return f"Customer message to {cfg()['name']}: " + ", ".join(names) + ", pickup, delivery."


class OpenAISTT:
    """gpt-4o-transcribe by default: far better than whisper-1 outside the big languages (Bangla among them).
    whisper-1 is still there with OPENAI_STT_MODEL=whisper-1. A business can set voice_language (e.g. bn) when nearly all
    its customers speak one language; otherwise the model detects it."""

    def transcribe(self, audio_bytes: bytes) -> Transcript:
        model = env("OPENAI_STT_MODEL", "gpt-4o-transcribe")
        whisper = model.startswith("whisper")
        lang = cfg().get("voice_language")
        data = {"model": model, "prompt": stt_hint(), **({"language": lang} if lang else {}),
                **({"response_format": "verbose_json"} if whisper else {"response_format": "json", "include[]": "logprobs"})}
        r = httpx.post(f"{OPENAI_URL}/transcriptions", timeout=120, headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"},
                       data=data, files={"file": ("voice.wav", audio_bytes, "audio/wav")})
        r.raise_for_status()
        j = r.json()
        if whisper:
            segs = j.get("segments") or []
            conf = sum(math.exp(s["avg_logprob"]) * (1 - s.get("no_speech_prob", 0)) for s in segs) / len(segs) if segs else None
            return Transcript(j.get("text", "").strip(), j.get("language"), conf)
        probs = [math.exp(t["logprob"]) for t in j.get("logprobs") or []]  # one per token of the transcript
        return Transcript(j.get("text", "").strip(), lang, sum(probs) / len(probs) if probs else None)


def get_stt() -> OpenAISTT:
    return OpenAISTT()
