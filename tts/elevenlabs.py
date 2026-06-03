import asyncio
import aiohttp
import subprocess
import tempfile
import os
from pathlib import Path

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"


async def text_to_pcm(text: str, voice_id: str, api_key: str) -> bytes:
    """Convert text to PCM 16kHz mono via ElevenLabs REST API."""
    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "output_format": "pcm_16000",  # raw PCM 16kHz 16-bit mono
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8,
            "style": 0.3,
            "use_speaker_boost": True,
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 401:
                raise PermissionError(
                    "ElevenLabs API key missing 'text_to_speech' permission. "
                    "Go to elevenlabs.io → Profile → API Keys → create a key with TTS permission."
                )
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"ElevenLabs error {resp.status}: {body}")
            return await resp.read()


async def text_to_pcm_macos(text: str) -> bytes:
    """Fallback: macOS `say` command → PCM 16kHz mono (for MVP testing without ElevenLabs)."""
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f:
        aiff_path = f.name
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        raw_path = f.name

    try:
        # Generate AIFF with macOS TTS (Meijia voice for Chinese)
        proc = await asyncio.create_subprocess_exec(
            "say", "-v", "Meijia", "-o", aiff_path, "--", text,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        # Convert to PCM 16kHz mono using afconvert (built-in macOS)
        proc2 = await asyncio.create_subprocess_exec(
            "afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
            aiff_path, raw_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc2.wait()

        # Read WAV and strip 44-byte header to get raw PCM
        data = Path(raw_path).read_bytes()
        return data[44:] if len(data) > 44 else data

    finally:
        for p in [aiff_path, raw_path]:
            try:
                os.unlink(p)
            except Exception:
                pass
