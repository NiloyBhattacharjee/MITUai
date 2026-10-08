"""Speech-to-text / text-to-speech behind small interfaces, plus ffmpeg helpers."""
import io
import math
import subprocess
import tempfile
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from .config import STT_PROVIDER, env


@dataclass
class Transcript:
    text: str
    language: str | None
    confidence: float | None  # 0..1, None if the provider gives none


class SpeechToText(Protocol):
    def transcribe(self, audio_bytes: bytes) -> Transcript: ...  # audio_bytes: 16 kHz mono WAV


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> tuple[bytes, str]: ...  # (audio, file extension)


# --- ffmpeg -------------------------------------------------------------------------------

def _ffmpeg(data: bytes, in_ext: str, args: list[str], out_ext: str) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / f"in.{in_ext}", Path(d) / f"out.{out_ext}"
        src.write_bytes(data)  # temp files, not pipes: mp4/webm from browsers need seekable input
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), *args, str(dst)], check=True)
        return dst.read_bytes()


def to_wav(data: bytes, ext: str) -> bytes:
    return _ffmpeg(data, ext, ["-ac", "1", "-ar", "16000"], "wav")


def to_ogg_opus(data: bytes, ext: str) -> bytes:
    return _ffmpeg(data, ext, ["-ac", "1", "-c:a", "libopus", "-b:a", "32k"], "ogg")


def wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / w.getframerate()


# --- providers ----------------------------------------------------------------------------

OPENAI_URL = "https://api.openai.com/v1/audio"
STT_HINT = "Customer message to a Bangladeshi clothing shop, in Bangla or English: panjabi, kurti, saree, three-piece, bKash, Nagad, delivery, Dhaka."


class OpenAISTT:
    def transcribe(self, audio_bytes: bytes) -> Transcript:
        r = httpx.post(f"{OPENAI_URL}/transcriptions", timeout=120,
                       headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"},
                       data={"model": env("OPENAI_STT_MODEL", "whisper-1"), "response_format": "verbose_json", "prompt": STT_HINT},
                       files={"file": ("voice.wav", audio_bytes, "audio/wav")})
        r.raise_for_status()
        j = r.json()
        segs = j.get("segments") or []
        conf = sum(math.exp(s["avg_logprob"]) * (1 - s.get("no_speech_prob", 0)) for s in segs) / len(segs) if segs else None
        return Transcript(j.get("text", "").strip(), j.get("language"), conf)


class AWSTranscribeSTT:
    def __init__(self):
        import boto3
        region = env("AWS_REGION", "us-east-1")
        self.s3, self.tr = boto3.client("s3", region_name=region), boto3.client("transcribe", region_name=region)
        self.bucket = env("TRANSCRIBE_S3_BUCKET")

    def transcribe(self, audio_bytes: bytes) -> Transcript:
        name = f"mitu-{uuid.uuid4().hex}"
        key = f"voice-notes/{name}.wav"
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=audio_bytes)
        try:
            self.tr.start_transcription_job(TranscriptionJobName=name, MediaFormat="wav",
                                            Media={"MediaFileUri": f"s3://{self.bucket}/{key}"},
                                            IdentifyLanguage=True, LanguageOptions=["bn-IN", "en-US"])
            for _ in range(180):  # batch jobs usually take 10-30 s
                job = self.tr.get_transcription_job(TranscriptionJobName=name)["TranscriptionJob"]
                if job["TranscriptionJobStatus"] in ("COMPLETED", "FAILED"):
                    break
                time.sleep(1)
            if job["TranscriptionJobStatus"] != "COMPLETED":
                raise RuntimeError(f"Transcribe job {name}: {job.get('FailureReason', 'timed out')}")
            res = httpx.get(job["Transcript"]["TranscriptFileUri"], timeout=30).json()["results"]
            confs = [float(i["alternatives"][0]["confidence"]) for i in res["items"] if i["type"] == "pronunciation"]
            return Transcript(res["transcripts"][0]["transcript"].strip(), job.get("LanguageCode"),
                              sum(confs) / len(confs) if confs else None)
        finally:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
            try:
                self.tr.delete_transcription_job(TranscriptionJobName=name)
            except Exception:
                pass


class OpenAITTS:
    def synthesize(self, text: str) -> tuple[bytes, str]:
        r = httpx.post(f"{OPENAI_URL}/speech", timeout=120,
                       headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"},
                       json={"model": env("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"), "voice": env("OPENAI_TTS_VOICE", "coral"),
                             "input": text, "response_format": "mp3"})
        r.raise_for_status()
        return r.content, "mp3"


def get_stt() -> SpeechToText:
    return AWSTranscribeSTT() if STT_PROVIDER == "aws" else OpenAISTT()


def get_tts() -> TextToSpeech:
    return OpenAITTS()
