"""Run every speech-to-text provider on the same voice notes and print the results side by side.
Usage: python compare_stt.py media/*.ogg      (needs ffmpeg plus OpenAI and AWS credentials)
"""
import sys
import time
from pathlib import Path

from app import shops, tenancy, voice

sys.stdout.reconfigure(encoding="utf-8")
providers = {"openai": voice.OpenAISTT(), "aws": voice.AWSTranscribeSTT()}
with tenancy.scope(shops.pick()):  # the first café's menu words go into the speech-to-text hint
    for path in map(Path, sys.argv[1:]):
        wav = voice.to_wav(path.read_bytes(), path.suffix.lstrip("."))
        print(f"\n== {path.name} ({voice.wav_seconds(wav):.0f}s)")
        for name, stt in providers.items():
            t = time.perf_counter()
            try:
                r = stt.transcribe(wav)
                print(f"  {name:6} {time.perf_counter() - t:5.1f}s  lang={r.language}  conf={r.confidence and round(r.confidence, 2)}  {r.text}")
            except Exception as e:
                print(f"  {name:6} failed: {e}")
