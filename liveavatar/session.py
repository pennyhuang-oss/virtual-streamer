"""
LiveAvatar LITE Mode session manager — with debug logging & timeouts.

Flow:
  1. POST /v1/sessions/token  → session_id + session_token (JWT)
  2. POST /v1/sessions/start  → livekit_url + livekit_client_token
  3. livekit rtc.Room().connect() → join LiveKit room
  4. publish LocalAudioTrack → ready to send PCM audio
  5. POST /v1/sessions/stop to end
"""

import asyncio
import struct
import time
from urllib.parse import urlencode
from typing import Optional

import aiohttp
from livekit import rtc

LIVEAVATAR_BASE = "https://api.liveavatar.com"
SAMPLE_RATE      = 16000
NUM_CHANNELS     = 1
SAMPLES_PER_FRAME = 1600   # 100 ms at 16 kHz

# Per-step timeouts (seconds)
T_HTTP   = 15
T_LK     = 20
T_PUBLISH = 15


def _dbg(msg: str, debug: bool) -> None:
    if debug:
        print(f"  [DEBUG] {msg}", flush=True)


class LiveAvatarSession:
    def __init__(self, api_key: str, avatar_id: str, debug: bool = False):
        self.api_key   = api_key
        self.avatar_id = avatar_id
        self.debug     = debug

        self.session_id:            Optional[str] = None
        self._session_token:        Optional[str] = None
        self._livekit_url:          Optional[str] = None
        self._livekit_client_token: Optional[str] = None

        self._room:         Optional[rtc.Room]        = None
        self._audio_source: Optional[rtc.AudioSource] = None

        self._created_at: float = 0
        self.credits_used: float = 0.0

    @property
    def _api_headers(self) -> dict:
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    @property
    def _bearer_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._session_token}",
            "Content-Type": "application/json",
        }

    # ── Step 1 ────────────────────────────────────────────────────────────────

    async def create(self) -> None:
        """POST /v1/sessions/token → get session_id + JWT token."""
        _dbg(f"POST {LIVEAVATAR_BASE}/v1/sessions/token  (timeout={T_HTTP}s)", self.debug)
        async with aiohttp.ClientSession() as http:
            resp = await asyncio.wait_for(
                http.post(
                    f"{LIVEAVATAR_BASE}/v1/sessions/token",
                    json={"mode": "LITE", "avatar_id": self.avatar_id},
                    headers=self._api_headers,
                ),
                timeout=T_HTTP,
            )
            data = await resp.json()
            _dbg(f"sessions/token → HTTP {resp.status}", self.debug)

            if resp.status != 200:
                raise RuntimeError(
                    f"sessions/token failed {resp.status}: "
                    f"{data.get('message', data)}"
                )
            d = data["data"]
            self.session_id      = d["session_id"]
            self._session_token  = d["session_token"]
            self._created_at     = time.time()
            _dbg(f"session_id = {self.session_id}", self.debug)

    # ── Step 2 ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """POST /v1/sessions/start → get LiveKit URL + client token."""
        _dbg(f"POST {LIVEAVATAR_BASE}/v1/sessions/start  (timeout={T_HTTP}s)", self.debug)
        async with aiohttp.ClientSession() as http:
            resp = await asyncio.wait_for(
                http.post(
                    f"{LIVEAVATAR_BASE}/v1/sessions/start",
                    headers=self._bearer_headers,
                ),
                timeout=T_HTTP,
            )
            data = await resp.json()
            _dbg(f"sessions/start → HTTP {resp.status}", self.debug)

            if resp.status == 403:
                msg = data.get("message", "")
                if "credits" in msg.lower():
                    raise RuntimeError(
                        "LiveAvatar credits 不足！\n"
                        "  請到 liveavatar.com 檢查帳戶餘額或升級方案。\n"
                        f"  (API 回應: {msg})"
                    )
                raise RuntimeError(f"sessions/start 403: {msg}")

            if resp.status == 403 or resp.status != 201:
                raise RuntimeError(
                    f"sessions/start failed {resp.status}: "
                    f"{data.get('message', data)}"
                )

            d = data["data"]
            self._livekit_url          = d["livekit_url"]
            self._livekit_client_token = d["livekit_client_token"]
            _dbg(f"livekit_url = {self._livekit_url}", self.debug)

    # ── Step 3 ────────────────────────────────────────────────────────────────

    async def connect_ws(self) -> None:
        """Join LiveKit room and publish audio track."""
        _dbg(f"rtc.Room().connect({self._livekit_url})  (timeout={T_LK}s)", self.debug)
        self._room         = rtc.Room()
        self._audio_source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)
        audio_track        = rtc.LocalAudioTrack.create_audio_track("tts-audio", self._audio_source)
        options            = rtc.RoomOptions(auto_subscribe=False)

        await asyncio.wait_for(
            self._room.connect(self._livekit_url, self._livekit_client_token, options),
            timeout=T_LK,
        )
        _dbg("LiveKit room connected", self.debug)

        _dbg(f"publish_track  (timeout={T_PUBLISH}s)", self.debug)
        pub_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await asyncio.wait_for(
            self._room.local_participant.publish_track(audio_track, pub_options),
            timeout=T_PUBLISH,
        )
        _dbg("audio track published", self.debug)

    # ── Audio ─────────────────────────────────────────────────────────────────

    async def send_audio(self, pcm_data: bytes) -> None:
        """Push PCM bytes to LiveKit as 100 ms frames."""
        if not self._audio_source:
            return
        bytes_per_sample = 2
        frame_bytes      = SAMPLES_PER_FRAME * NUM_CHANNELS * bytes_per_sample

        for i in range(0, len(pcm_data), frame_bytes):
            chunk = pcm_data[i : i + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk = chunk + b"\x00" * (frame_bytes - len(chunk))

            frame = rtc.AudioFrame(
                data=bytearray(chunk),
                sample_rate=SAMPLE_RATE,
                num_channels=NUM_CHANNELS,
                samples_per_channel=SAMPLES_PER_FRAME,
            )
            await self._audio_source.capture_frame(frame)

        duration_minutes   = len(pcm_data) / (SAMPLE_RATE * bytes_per_sample * NUM_CHANNELS) / 60
        self.credits_used += duration_minutes

    # ── Keep-alive ────────────────────────────────────────────────────────────

    async def keep_alive(self) -> None:
        async with aiohttp.ClientSession() as http:
            await asyncio.wait_for(
                http.post(
                    f"{LIVEAVATAR_BASE}/v1/sessions/keep-alive",
                    json={"session_id": self.session_id},
                    headers=self._bearer_headers,
                ),
                timeout=T_HTTP,
            )

    # ── Stop ──────────────────────────────────────────────────────────────────

    async def stop(self) -> None:
        if self._room:
            try:
                await self._room.disconnect()
            except Exception:
                pass
            self._room         = None
            self._audio_source = None

        if self.session_id:
            try:
                async with aiohttp.ClientSession() as http:
                    await asyncio.wait_for(
                        http.post(
                            f"{LIVEAVATAR_BASE}/v1/sessions/stop",
                            json={"session_id": self.session_id, "reason": "USER_CLOSED"},
                            headers=self._api_headers,
                        ),
                        timeout=T_HTTP,
                    )
            except Exception:
                pass
            self.session_id = None

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def livekit_url(self) -> Optional[str]:
        return self._livekit_url

    @property
    def livekit_client_token(self) -> Optional[str]:
        return self._livekit_client_token

    @property
    def browser_preview_url(self) -> Optional[str]:
        """
        meet.livekit.io viewer URL — open in Chrome to see avatar video.
        Also paste into OBS Browser Source.
        """
        if not self._livekit_url or not self._livekit_client_token:
            return None
        params = urlencode({
            "liveKitUrl": self._livekit_url,
            "token":      self._livekit_client_token,
        })
        return f"https://meet.livekit.io/custom?{params}"

    def age_minutes(self) -> float:
        return (time.time() - self._created_at) / 60 if self._created_at else 0

    def update_credits(self, elapsed_minutes: float) -> None:
        self.credits_used += elapsed_minutes
