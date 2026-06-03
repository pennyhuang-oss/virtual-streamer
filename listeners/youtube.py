"""
YouTube Live Chat Listener
──────────────────────────
Uses YouTube Data API v3 liveChatMessages.list
  - Polls every max(3s, pollingIntervalMillis from API response)
  - Deduplicates via seen message IDs
  - Handles pageToken pagination

Required env vars:
  YOUTUBE_API_KEY        — YouTube Data API v3 key
  YOUTUBE_LIVE_CHAT_ID   — liveChatId from the live broadcast
"""

import asyncio
import aiohttp
from datetime import datetime
from rich.console import Console
from .base import Comment, CommentCallback

console = Console()

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
DEFAULT_POLL_INTERVAL = 3.0   # seconds


class YouTubeChatListener:
    def __init__(self, api_key: str, live_chat_id: str):
        self.api_key = api_key
        self.live_chat_id = live_chat_id
        self._seen_ids: set[str] = set()
        self._page_token: str | None = None
        self._poll_interval: float = DEFAULT_POLL_INTERVAL

    async def listen(self, callback: CommentCallback) -> None:
        """Poll YouTube Live Chat indefinitely, fire callback for each new message."""
        console.print(f"[green]▶ YouTube Chat 監聽啟動[/] chatId: {self.live_chat_id[:20]}...")

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    messages, next_token, interval_ms = await self._fetch(session)
                    self._page_token = next_token
                    self._poll_interval = max(DEFAULT_POLL_INTERVAL, interval_ms / 1000)

                    for msg in messages:
                        msg_id = msg["id"]
                        if msg_id in self._seen_ids:
                            continue
                        self._seen_ids.add(msg_id)

                        author = msg["authorDetails"]["displayName"]
                        text   = msg["snippet"]["displayMessage"]
                        ts_str = msg["snippet"]["publishedAt"]  # ISO 8601

                        comment = Comment(
                            platform="youtube",
                            viewer_name=author,
                            message=text,
                            timestamp=datetime.fromisoformat(ts_str.replace("Z", "+00:00")),
                        )
                        console.print(f"  [dim][YouTube][/] {author}: {text[:60]}")
                        await callback(comment)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    console.print(f"[yellow]YouTube 輪詢錯誤（繼續重試）: {e}[/]")

                await asyncio.sleep(self._poll_interval)

    async def _fetch(self, session: aiohttp.ClientSession) -> tuple[list, str | None, int]:
        params = {
            "part": "snippet,authorDetails",
            "liveChatId": self.live_chat_id,
            "key": self.api_key,
            "maxResults": 200,
        }
        if self._page_token:
            params["pageToken"] = self._page_token

        async with session.get(f"{YOUTUBE_API_BASE}/liveChat/messages", params=params) as resp:
            if resp.status == 403:
                body = await resp.json()
                raise RuntimeError(f"YouTube API 403: {body.get('error', {}).get('message', '')}")
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"YouTube API {resp.status}: {body[:200]}")

            data = await resp.json()
            messages      = data.get("items", [])
            next_token    = data.get("nextPageToken")
            interval_ms   = data.get("pollingIntervalMillis", 3000)
            return messages, next_token, interval_ms
