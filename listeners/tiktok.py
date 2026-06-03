"""
TikTok Live Chat Listener
─────────────────────────
Uses TikTokLive 6.x (event-driven WebSocket)
  - Connects to a live stream by TikTok @username
  - Fires callback on every CommentEvent
  - Reconnects automatically on disconnect

Required env var:
  TIKTOK_USERNAME  — TikTok username (with or without @)
"""

import asyncio
from datetime import datetime
from rich.console import Console
from .base import Comment, CommentCallback

console = Console()


class TikTokChatListener:
    def __init__(self, username: str):
        # Normalize: always include @
        self.username = username if username.startswith("@") else f"@{username}"

    async def listen(self, callback: CommentCallback) -> None:
        """Connect to TikTok Live and fire callback on each comment."""
        try:
            from TikTokLive.client.client import TikTokLiveClient
            from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent
        except ImportError:
            console.print("[red]TikTokLive 未安裝：pip install TikTokLive[/]")
            return

        console.print(f"[green]▶ TikTok Chat 監聽啟動[/] 用戶: {self.username}")

        while True:
            client = TikTokLiveClient(unique_id=self.username)

            @client.on(ConnectEvent)
            async def on_connect(event: ConnectEvent) -> None:
                console.print(f"[green]✓ TikTok 連線成功[/] → {self.username}")

            @client.on(CommentEvent)
            async def on_comment(event: CommentEvent) -> None:
                viewer = getattr(event.user, "nickname", None) \
                      or getattr(event.user, "unique_id", "觀眾")
                text = event.comment
                comment = Comment(
                    platform="tiktok",
                    viewer_name=viewer,
                    message=text,
                    timestamp=datetime.now(),
                )
                console.print(f"  [dim][TikTok][/] {viewer}: {text[:60]}")
                await callback(comment)

            @client.on(DisconnectEvent)
            async def on_disconnect(event: DisconnectEvent) -> None:
                console.print("[yellow]TikTok 斷線，5 秒後重連...[/]")

            try:
                await client.start()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                console.print(f"[yellow]TikTok 連線錯誤（5 秒後重試）: {e}[/]")

            await asyncio.sleep(5)
