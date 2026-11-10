"""Speech-to-text (OpenAI Whisper), plus ffmpeg helpers."""
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
    def transcribe(self, audio_bytes: bytes) -> Transcript:
        r = httpx.post(f"{OPENAI_URL}/transcriptions", timeout=120,
                       headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"},
                       data={"model": env("OPENAI_STT_MODEL", "whisper-1"), "response_format": "verbose_json", "prompt": stt_hint()},
                       files={"file": ("voice.wav", audio_bytes, "audio/wav")})
        r.raise_for_status()
        j = r.json()
        segs = j.get("segments") or []
        conf = sum(math.exp(s["avg_logprob"]) * (1 - s.get("no_speech_prob", 0)) for s in segs) / len(segs) if segs else None
        return Transcript(j.get("text", "").strip(), j.get("language"), conf)


def get_stt() -> OpenAISTT:
    return OpenAISTT()
