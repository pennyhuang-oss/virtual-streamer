"""
LiveAvatar LITE Mode session manager.

New LiveAvatar API flow (post-March 2026 migration):
  1. POST /v1/sessions/token  (x-api-key auth)
     → session_id + session_token (JWT)
  2. POST /v1/sessions/start  (Bearer session_token)
     → session_id + livekit_url + livekit_client_token + livekit_agent_token
  3. Join LiveKit room with livekit_client_token, publish PCM audio frames
  4. Avatar renders video in the LiveKit room
     → View via LiveKit URL in OBS Browser Source or the LiveAvatar dashboard
  5. POST /v1/sessions/stop to end

Audio format: PCM 16kHz, 16-bit, mono (matches ElevenLabs pcm_16000 output)
"""

import asyncio
import struct
import time
import aiohttp
from typing import Optional

from livekit import rtc

LIVEAVATAR_BASE = "https://api.liveavatar.com"
SAMPLE_RATE = 16000
NUM_CHANNELS = 1
SAMPLES_PER_FRAME = 1600  # 100ms frames at 16kHz


class LiveAvatarSession:
    def __init__(self, api_key: str, avatar_id: str):
        self.api_key = api_key
        self.avatar_id = avatar_id

        self.session_id: Optional[str] = None
        self._session_token: Optional[str] = None
        self._livekit_url: Optional[str] = None
        self._livekit_client_token: Optional[str] = None

        self._room: Optional[rtc.Room] = None
        self._audio_source: Optional[rtc.AudioSource] = None

        self._created_at: float = 0
        self.credits_used: int = 0

    @property
    def _api_headers(self) -> dict:
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    @property
    def _bearer_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._session_token}",
            "Content-Type": "application/json",
        }

    async def create(self) -> None:
        """Step 1: Obtain a session token."""
        async with aiohttp.ClientSession() as http:
            resp = await http.post(
                f"{LIVEAVATAR_BASE}/v1/sessions/token",
                json={"mode": "LITE", "avatar_id": self.avatar_id},
                headers=self._api_headers,
            )
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"sessions/token failed {resp.status}: {data}")
            d = data["data"]
            self.session_id = d["session_id"]
            self._session_token = d["session_token"]
            self._created_at = time.time()

    async def start(self) -> None:
        """Step 2: Start the session and get LiveKit credentials."""
        async with aiohttp.ClientSession() as http:
            resp = await http.post(
                f"{LIVEAVATAR_BASE}/v1/sessions/start",
                headers=self._bearer_headers,
            )
            data = await resp.json()
            if resp.status != 201:
                raise RuntimeError(f"sessions/start failed {resp.status}: {data}")
            d = data["data"]
            self._livekit_url = d["livekit_url"]
            self._livekit_client_token = d["livekit_client_token"]

    async def connect_ws(self) -> None:
        """Step 3: Join LiveKit room and set up audio source."""
        self._room = rtc.Room()
        self._audio_source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)

        audio_track = rtc.LocalAudioTrack.create_audio_track(
            "tts-audio", self._audio_source
        )
        options = rtc.RoomOptions(auto_subscribe=False)
        await self._room.connect(self._livekit_url, self._livekit_client_token, options)

        pub_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self._room.local_participant.publish_track(audio_track, pub_options)

    async def send_audio(self, pcm_data: bytes) -> None:
        """Push PCM bytes to LiveKit as 100ms audio frames."""
        if not self._audio_source:
            return

        bytes_per_sample = 2  # 16-bit
        frame_bytes = SAMPLES_PER_FRAME * NUM_CHANNELS * bytes_per_sample

        for i in range(0, len(pcm_data), frame_bytes):
            chunk = pcm_data[i : i + frame_bytes]
            # Pad last frame if needed
            if len(chunk) < frame_bytes:
                chunk = chunk + b"\x00" * (frame_bytes - len(chunk))

            # Convert bytes → list[int16]
            samples_count = len(chunk) // bytes_per_sample
            samples = list(struct.unpack(f"<{samples_count}h", chunk))

            frame = rtc.AudioFrame(
                data=bytearray(chunk),
                sample_rate=SAMPLE_RATE,
                num_channels=NUM_CHANNELS,
                samples_per_channel=SAMPLES_PER_FRAME,
            )
            await self._audio_source.capture_frame(frame)

        # Calculate credits: 1 credit per minute of avatar rendering
        duration_minutes = len(pcm_data) / (SAMPLE_RATE * bytes_per_sample * NUM_CHANNELS) / 60
        self.credits_used += duration_minutes

    async def keep_alive(self) -> None:
        """Keep session alive (call periodically if needed)."""
        async with aiohttp.ClientSession() as http:
            await http.post(
                f"{LIVEAVATAR_BASE}/v1/sessions/keep-alive",
                json={"session_id": self.session_id},
                headers=self._bearer_headers,
            )

    async def stop(self) -> None:
        """Disconnect from LiveKit and stop the LiveAvatar session."""
        if self._room:
            try:
                await self._room.disconnect()
            except Exception:
                pass
            self._room = None
            self._audio_source = None

        if self.session_id:
            async with aiohttp.ClientSession() as http:
                # API key auth also works for stop (more reliable than bearer)
                await http.post(
                    f"{LIVEAVATAR_BASE}/v1/sessions/stop",
                    json={"session_id": self.session_id, "reason": "USER_CLOSED"},
                    headers=self._api_headers,
                )
            self.session_id = None

    def age_minutes(self) -> float:
        return (time.time() - self._created_at) / 60 if self._created_at else 0

    def update_credits(self, elapsed_minutes: float) -> None:
        self.credits_used += elapsed_minutes
