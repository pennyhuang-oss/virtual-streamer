"""
Virtual Streamer — AI 虛擬主播直播系統
========================================
Usage:
  python main.py --avatar katya --platform test    --theme 測試直播
  python main.py --avatar katya --platform youtube --chat-id <LIVE_CHAT_ID>
  python main.py --avatar katya --platform tiktok  --username <TIKTOK_USERNAME>
"""

import argparse
import asyncio
import os
import re
import sys
import time
import json
import datetime
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich import box

from agents.script_agent import ScriptAgent
from agents.comment_handler import CommentHandler
from tts.elevenlabs import text_to_pcm, text_to_pcm_macos
from liveavatar.session import LiveAvatarSession

load_dotenv()
console = Console()

BASE_DIR    = Path(__file__).parent
AVATARS_DIR = BASE_DIR / "avatars"
SCRIPTS_DIR = BASE_DIR / "scripts"
LOGS_DIR    = BASE_DIR / "logs" / "sessions"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ── data ────────────────────────────────────────────────────────────────────

@dataclass
class SpeakItem:
    text:   str
    source: str   # "auto" | "manual" | "youtube" | "tiktok"
    label:  str = ""


# ── loaders ─────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def load_avatar(name: str) -> dict:
    path = AVATARS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Avatar config not found: {path}")
    with open(path) as f:
        raw = f.read()
    raw = re.sub(r'\$\{(\w+)\}', lambda m: os.getenv(m.group(1), ""), raw)
    return yaml.safe_load(raw)


def get_api_keys() -> tuple[str, str, str]:
    la_key = os.getenv("LIVEAVATAR_API_KEY", "")
    an_key = os.getenv("ANTHROPIC_API_KEY", "")
    el_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not la_key:
        raise ValueError("LIVEAVATAR_API_KEY not set in .env")
    return la_key, an_key, el_key


# ── main session ─────────────────────────────────────────────────────────────

class StreamingSession:
    def __init__(
        self,
        avatar:   dict,
        theme:    str,
        cfg:      dict,
        platform: str,
        la_key:   str,
        an_key:   str,
        el_key:   str,
    ):
        self.avatar    = avatar
        self.theme     = theme
        self.cfg       = cfg
        self.platform  = platform
        self.la_key    = la_key
        self.el_key    = el_key
        self.context   = {"theme": theme, "viewer_name": "大家"}

        # Claude client (optional)
        self._claude = None
        if an_key and an_key != "your_anthropic_api_key_here":
            try:
                import anthropic
                self._claude = anthropic.Anthropic(api_key=an_key)
            except Exception:
                pass

        self.agent = ScriptAgent(
            avatar_config=avatar,
            claude_api_key=an_key,
            claude_model=cfg["claude"]["model"],
        )
        self.comment_handler = CommentHandler(
            avatar_name=avatar["name"],
            scripts_base=SCRIPTS_DIR,
            claude_client=self._claude,
            claude_model="claude-haiku-4-5",
        )
        self.la_session = LiveAvatarSession(
            api_key=la_key,
            avatar_id=avatar["avatar_id"],
        )

        self.start_time  = time.time()
        self.total_credits = 0.0
        self.script_count  = 0
        self._running      = False
        self._speaking     = False
        self._speak_queue: asyncio.Queue[SpeakItem] = asyncio.Queue()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _elapsed(self) -> str:
        s = int(time.time() - self.start_time)
        return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

    def _log(self, source: str, text: str) -> None:
        entry = {
            "time":    datetime.datetime.now().isoformat(),
            "elapsed": self._elapsed(),
            "source":  source,
            "text":    text,
            "credits": round(self.total_credits, 3),
        }
        log_file = LOGS_DIR / f"{datetime.date.today()}_{self.avatar['name']}.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _print_speak(self, item: SpeakItem) -> None:
        colour = {
            "auto":    "cyan",
            "manual":  "magenta",
            "youtube": "red",
            "tiktok":  "bright_cyan",
        }.get(item.source, "white")

        icons = {"auto": "🤖", "manual": "✍", "youtube": "▶", "tiktok": "🎵"}
        icon  = icons.get(item.source, "●")
        label = item.label or item.source.upper()

        tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        tbl.add_column(style="dim"); tbl.add_column()
        tbl.add_row("時間",   self._elapsed())
        tbl.add_row("Credits", f"{self.total_credits:.2f}")
        tbl.add_row("腳本",  str(self.script_count))
        console.print(tbl)
        console.print(Panel(
            item.text,
            title=f"[bold {colour}]{icon} {self.avatar['display_name']} [{label}][/]",
            border_style=colour,
        ))

    # ── TTS + send ────────────────────────────────────────────────────────────

    async def _tts_and_send(self, text: str) -> None:
        voice_id = self.avatar.get("voice_id", "")
        try:
            if voice_id and self.el_key:
                pcm = await text_to_pcm(text, voice_id, self.el_key)
            else:
                if not hasattr(self, "_warned_tts"):
                    self._warned_tts = True
                    console.print("[yellow]⚠ 使用 macOS TTS（ElevenLabs voice_id 未設定）[/]")
                pcm = await text_to_pcm_macos(text)
            await self.la_session.send_audio(pcm)
            self.total_credits = self.la_session.credits_used
        except Exception as e:
            console.print(f"[red]TTS/Audio error: {e}[/]")

    # ── speak methods ─────────────────────────────────────────────────────────

    async def _speak_auto(self, trigger: str) -> None:
        """Auto-triggered (timer-based) script speak."""
        self._speaking = True
        try:
            text = await self.agent.get_script(trigger, self.context)
            self.script_count += 1
            await self._tts_and_send(text)
            self._print_speak(SpeakItem(text, "auto", trigger))
            self._log("auto", text)
        finally:
            self._speaking = False

    async def _speak_item(self, item: SpeakItem) -> None:
        """Speak a queued SpeakItem (manual or comment)."""
        self._speaking = True
        try:
            await self._tts_and_send(item.text)
            self._print_speak(item)
            self._log(item.source, item.text)
        except Exception as e:
            console.print(f"[red]speak_item error: {e}[/]")
        finally:
            self._speaking = False

    # ── comment callback ──────────────────────────────────────────────────────

    async def on_comment(self, comment) -> None:
        """Receives a Comment from any listener, generates reply, enqueues."""
        try:
            text = await self.comment_handler.handle(comment, self.context)
            await self._speak_queue.put(SpeakItem(
                text=text,
                source=comment.platform,
                label=comment.viewer_name,
            ))
        except Exception as e:
            console.print(f"[yellow]comment handler error: {e}[/]")

    # ── background workers ────────────────────────────────────────────────────

    async def _queue_worker(self) -> None:
        """Process speak queue one item at a time."""
        while self._running:
            try:
                item = await asyncio.wait_for(self._speak_queue.get(), timeout=1.0)
                await self._speak_item(item)
                self._speak_queue.task_done()
            except asyncio.TimeoutError:
                continue

    async def _timer_loop(self, session_start: float) -> None:
        """Auto-trigger main content and CTA on timers."""
        max_min      = self.cfg["timers"]["session_renew_minutes"]
        main_iv      = self.cfg["timers"]["main_script_interval"]
        cta_iv       = self.cfg["timers"]["cta_interval"]
        last_main    = time.time()
        last_cta     = time.time()

        while self._running:
            now = time.time()
            if (now - session_start) / 60 >= max_min:
                await self._renew_session()
                session_start = now
            if now - last_main >= main_iv and not self._speaking:
                await self._speak_auto("main")
                last_main = time.time()
            if now - last_cta >= cta_iv and not self._speaking:
                await self._speak_auto("cta")
                last_cta = time.time()
            await asyncio.sleep(5)

    async def _input_loop(self) -> None:
        """Read manual text from stdin (test / interactive mode)."""
        loop = asyncio.get_event_loop()
        console.print("[dim]💬 輸入文字 + Enter → Katya 說出來　｜　q = 結束[/]\n")
        while self._running:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                text = line.strip()
                if not text:
                    continue
                if text.lower() == "q":
                    self._running = False
                    break
                await self._speak_queue.put(SpeakItem(text, "manual"))
            except (EOFError, KeyboardInterrupt):
                self._running = False
                break

    async def _renew_session(self) -> None:
        console.print("[yellow]⟳ 重建 LiveAvatar session...[/]")
        await self.la_session.stop()
        self.la_session = LiveAvatarSession(
            api_key=self.la_key,
            avatar_id=self.avatar["avatar_id"],
        )
        await self.la_session.create()
        await self.la_session.start()
        await self.la_session.connect_ws()
        console.print("[green]✓ Session 已重建[/]")

    # ── URL display ───────────────────────────────────────────────────────────

    def _print_urls(self, livekit_url: str, browser_url: str) -> None:
        console.print(Rule())

        # ① LiveKit Room URL（for OBS Browser Source）
        console.print(Panel(
            f"[bold white]{livekit_url}[/]",
            title="① LiveKit Room URL（貼入 OBS Browser Source 的 liveKitUrl 參數）",
            border_style="cyan",
            padding=(0, 1),
        ))

        # ② 瀏覽器直接開啟預覽
        console.print(Panel(
            f"[bold yellow]{browser_url}[/]",
            title="② 瀏覽器預覽 — 直接複製貼上到 Chrome / Safari 即可看到 Katya",
            border_style="yellow",
            padding=(0, 1),
        ))

        # OBS 步驟說明
        console.print(Panel(
            "1. 複製上方 [yellow]② 瀏覽器預覽連結[/yellow]\n"
            "2. OBS → 來源 → ＋ → [bold]瀏覽器（Browser Source）[/bold]\n"
            "3. 把連結貼入 URL 欄位\n"
            "4. 寬 [bold]1920[/bold]  ×  高 [bold]1080[/bold]，勾選「關閉時停止播放」\n"
            "5. 確定 → Katya 的畫面就會出現在 OBS",
            title="📺 OBS 設定（3 分鐘完成）",
            border_style="dim",
            padding=(0, 2),
        ))
        console.print(Rule())

    # ── main run ──────────────────────────────────────────────────────────────

    async def run(
        self,
        platform: str,
        youtube_chat_id: str = "",
        tiktok_username: str = "",
    ) -> None:
        self._running = True

        # ── Connect LiveAvatar ────────────────────────────────────────────────
        livekit_url  = ""
        browser_url  = ""
        try:
            console.print("[dim]建立 LiveAvatar session...[/]")
            await self.la_session.create()
            await self.la_session.start()
            await self.la_session.connect_ws()
            livekit_url = self.la_session.livekit_url or ""
            browser_url = self.la_session.browser_preview_url or ""
            console.print("[green]✓ LiveAvatar 連線成功[/]")
        except Exception as e:
            console.print(f"[red]LiveAvatar 連線失敗: {e}[/]")
            console.print("[yellow]繼續執行（無虛擬人渲染）...[/]")

        # ── Startup banner ────────────────────────────────────────────────────
        platform_icons = {"youtube": "▶ YouTube", "tiktok": "🎵 TikTok", "test": "✍ 測試模式"}
        console.print(Panel(
            f"[bold green]虛擬主播直播系統啟動[/]\n"
            f"Avatar  : [cyan]{self.avatar['display_name']}[/]   主題: [cyan]{self.theme}[/]\n"
            f"平台    : [bold white]{platform_icons.get(platform, platform)}[/]\n"
            f"Avatar ID: [dim]{self.avatar['avatar_id']}[/]",
            title="✦ VIRTUAL STREAMER ✦",
            border_style="green",
        ))

        if livekit_url and browser_url:
            self._print_urls(livekit_url, browser_url)

        # ── Opening ───────────────────────────────────────────────────────────
        await self._speak_auto("opening")
        session_start = time.time()

        # ── Build coroutine list ──────────────────────────────────────────────
        tasks = [
            self._timer_loop(session_start),
            self._queue_worker(),
        ]

        if platform == "test":
            tasks.append(self._input_loop())

        elif platform == "youtube":
            if not youtube_chat_id:
                youtube_chat_id = os.getenv("YOUTUBE_LIVE_CHAT_ID", "")
            if not youtube_chat_id:
                console.print("[red]缺少 YOUTUBE_LIVE_CHAT_ID（.env 或 --chat-id）[/]")
                youtube_chat_id = ""
            else:
                from listeners.youtube import YouTubeChatListener
                yt = YouTubeChatListener(
                    api_key=os.getenv("YOUTUBE_API_KEY", ""),
                    live_chat_id=youtube_chat_id,
                )
                tasks.append(yt.listen(self.on_comment))
            # Also keep manual input available
            tasks.append(self._input_loop())

        elif platform == "tiktok":
            if not tiktok_username:
                tiktok_username = os.getenv("TIKTOK_USERNAME", "")
            if not tiktok_username:
                console.print("[red]缺少 TIKTOK_USERNAME（.env 或 --username）[/]")
            else:
                from listeners.tiktok import TikTokChatListener
                tt = TikTokChatListener(username=tiktok_username)
                tasks.append(tt.listen(self.on_comment))
            tasks.append(self._input_loop())

        # ── Run all coroutines ────────────────────────────────────────────────
        try:
            await asyncio.gather(*tasks)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._running = False
            console.print("\n[dim]正在結束直播...[/]")
            await self._speak_auto("closing")
            await self.la_session.stop()
            console.print(
                f"[bold]直播結束｜Credits: {self.total_credits:.2f}"
                f"｜腳本數: {self.script_count}[/]"
            )


# ── entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="Virtual Streamer")
    parser.add_argument("--avatar",   required=True, help="Avatar name（e.g. katya）")
    parser.add_argument("--platform", required=True,
                        choices=["test", "youtube", "tiktok"],
                        help="直播平台：test / youtube / tiktok")
    parser.add_argument("--theme",    default="直播", help="直播主題（預設：直播）")
    parser.add_argument("--chat-id",  default="",     help="YouTube Live Chat ID")
    parser.add_argument("--username", default="",     help="TikTok @username")
    args = parser.parse_args()

    cfg    = load_config()
    avatar = load_avatar(args.avatar)
    la_key, an_key, el_key = get_api_keys()

    session = StreamingSession(
        avatar=avatar, theme=args.theme, cfg=cfg,
        platform=args.platform,
        la_key=la_key, an_key=an_key, el_key=el_key,
    )

    try:
        await session.run(
            platform=args.platform,
            youtube_chat_id=args.chat_id,
            tiktok_username=args.username,
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
