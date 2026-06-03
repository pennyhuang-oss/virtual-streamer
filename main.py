"""
virtual-streamer MVP
Usage: python main.py --avatar katya --theme 測試直播
"""

import argparse
import asyncio
import os
import sys
import time
import json
import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from agents.script_agent import ScriptAgent
from tts.elevenlabs import text_to_pcm, text_to_pcm_macos
from liveavatar.session import LiveAvatarSession

load_dotenv()
console = Console()

BASE_DIR = Path(__file__).parent
AVATARS_DIR = BASE_DIR / "avatars"
LOGS_DIR = BASE_DIR / "logs" / "sessions"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def load_avatar(name: str) -> dict:
    import re
    path = AVATARS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Avatar config not found: {path}")
    with open(path) as f:
        raw = f.read()
    # Expand ${ENV_VAR} placeholders from environment
    raw = re.sub(r'\$\{(\w+)\}', lambda m: os.getenv(m.group(1), ""), raw)
    return yaml.safe_load(raw)


def get_api_keys(cfg: dict) -> tuple[str, str, str]:
    la_key = os.getenv("LIVEAVATAR_API_KEY", "")
    an_key = os.getenv("ANTHROPIC_API_KEY", "")
    el_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not la_key:
        raise ValueError("LIVEAVATAR_API_KEY not set in .env")
    # ANTHROPIC_API_KEY optional — Claude fallback skipped if missing (uses script files only)
    # ElevenLabs is optional — fallback to macOS TTS if missing
    return la_key, an_key, el_key


class StreamingSession:
    def __init__(self, avatar: dict, theme: str, cfg: dict, la_key: str, an_key: str, el_key: str):
        self.avatar = avatar
        self.theme = theme
        self.cfg = cfg
        self.la_key = la_key
        self.el_key = el_key
        self.context = {"theme": theme, "viewer_name": "大家"}

        self.agent = ScriptAgent(
            avatar_config=avatar,
            claude_api_key=an_key,
            claude_model=cfg["claude"]["model"],
        )
        self.la_session = LiveAvatarSession(
            api_key=la_key,
            avatar_id=avatar["avatar_id"],
        )

        self.start_time = time.time()
        self.total_credits = 0
        self.script_count = 0
        self.log_entries: list[dict] = []
        self._running = False
        self._speak_queue: asyncio.Queue = asyncio.Queue()  # manual text input queue
        self._speaking = False  # guard: avoid overlapping audio

    def _elapsed(self) -> str:
        secs = int(time.time() - self.start_time)
        return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"

    def _log(self, trigger: str, text: str) -> None:
        entry = {
            "time": datetime.datetime.now().isoformat(),
            "elapsed": self._elapsed(),
            "trigger": trigger,
            "text": text,
            "credits": self.total_credits,
        }
        self.log_entries.append(entry)

        log_file = LOGS_DIR / f"{datetime.date.today()}_{self.avatar['name']}.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _print_status(self, trigger: str, text: str) -> None:
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column(style="dim")
        table.add_column()
        table.add_row("時間", self._elapsed())
        table.add_row("Avatar", self.avatar["display_name"])
        table.add_row("主題", self.theme)
        table.add_row("Credits", str(self.total_credits))
        table.add_row("腳本數", str(self.script_count))
        table.add_row("觸發", trigger)

        console.print(table)
        console.print(Panel(text, title=f"[bold cyan]{self.avatar['display_name']}[/]", border_style="cyan"))

    async def _tts_and_send(self, text: str) -> None:
        """Convert text → PCM → LiveAvatar. Shared by auto and manual speak."""
        voice_id = self.avatar.get("voice_id", "")
        try:
            if voice_id and self.el_key:
                pcm = await text_to_pcm(text, voice_id, self.el_key)
            else:
                if not hasattr(self, "_warned_tts"):
                    self._warned_tts = True
                    console.print("[yellow]⚠ 使用 macOS TTS 作為備用（voice_id 或 ElevenLabs key 未設定）[/]")
                pcm = await text_to_pcm_macos(text)
            await self.la_session.send_audio(pcm)
            self.total_credits = self.la_session.credits_used
        except Exception as e:
            console.print(f"[red]TTS/Audio error: {e}[/]")

    async def _speak(self, trigger: str) -> None:
        """Auto-triggered script speak."""
        self._speaking = True
        try:
            text = await self.agent.get_script(trigger, self.context)
            self.script_count += 1
            await self._tts_and_send(text)
            self._print_status(trigger, text)
            self._log(trigger, text)
        finally:
            self._speaking = False

    async def speak_text(self, text: str) -> None:
        """Manually speak arbitrary text (from terminal input)."""
        self._speaking = True
        try:
            console.print(f"\n[bold magenta]▶ 手動輸入[/] → 送出中...")
            await self._tts_and_send(text)
            console.print(Panel(
                text,
                title=f"[bold magenta]{self.avatar['display_name']} ✍ 手動[/]",
                border_style="magenta",
            ))
            self._log("manual", text)
        except Exception as e:
            console.print(f"[red]speak_text error: {e}[/]")
        finally:
            self._speaking = False

    async def _input_loop(self) -> None:
        """Read lines from stdin and queue them for speaking."""
        loop = asyncio.get_event_loop()
        console.print("[dim]💬 直接輸入文字 + Enter，讓 Katya 說出來。輸入 q 或 Ctrl+C 結束直播。[/]\n")
        while self._running:
            try:
                # Run blocking input() in a thread so it doesn't block asyncio
                line = await loop.run_in_executor(None, sys.stdin.readline)
                text = line.strip()
                if not text:
                    continue
                if text.lower() == "q":
                    self._running = False
                    break
                await self._speak_queue.put(text)
            except (EOFError, KeyboardInterrupt):
                self._running = False
                break

    async def _queue_worker(self) -> None:
        """Drain the manual speak queue one item at a time."""
        while self._running:
            try:
                text = await asyncio.wait_for(self._speak_queue.get(), timeout=1.0)
                await self.speak_text(text)
                self._speak_queue.task_done()
            except asyncio.TimeoutError:
                continue

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

    async def run(self) -> None:
        self._running = True
        max_min = self.cfg["timers"]["session_renew_minutes"]
        main_interval = self.cfg["timers"]["main_script_interval"]
        cta_interval = self.cfg["timers"]["cta_interval"]

        # Init LiveAvatar session
        livekit_url = ""
        try:
            console.print("[dim]建立 LiveAvatar session...[/]")
            await self.la_session.create()
            await self.la_session.start()
            await self.la_session.connect_ws()
            livekit_url = self.la_session.livekit_url or ""
            console.print("[green]✓ LiveAvatar 連線成功[/]")
        except Exception as e:
            console.print(f"[red]LiveAvatar 連線失敗: {e}[/]")
            console.print("[yellow]繼續執行（無虛擬人渲染）...[/]")

        # Startup banner — show LiveKit URL prominently
        viewer_line = (
            f"[bold yellow]🎥 瀏覽器預覽[/]: [link={livekit_url}]{livekit_url}[/link]"
            if livekit_url
            else "[dim]LiveKit URL 未取得[/]"
        )
        console.print(Panel(
            f"[bold green]虛擬主播直播系統啟動[/]\n"
            f"Avatar : [cyan]{self.avatar['display_name']}[/]   主題: [cyan]{self.theme}[/]\n"
            f"Avatar ID: [dim]{self.avatar['avatar_id']}[/]\n\n"
            f"{viewer_line}\n\n"
            f"[dim]💬 在此輸入文字 + Enter 讓 {self.avatar['display_name']} 說話｜輸入 q 結束[/]",
            title="✦ VIRTUAL STREAMER ✦",
            border_style="green",
        ))

        # Opening
        await self._speak("opening")

        last_main = time.time()
        last_cta = time.time()
        session_start = time.time()

        async def _main_loop():
            while self._running:
                now = time.time()
                if (now - session_start) / 60 >= max_min:
                    await self._renew_session()
                if now - last_main >= main_interval:
                    if not self._speaking:
                        await self._speak("main")
                    last_main_ref[0] = now
                if now - last_cta >= cta_interval:
                    if not self._speaking:
                        await self._speak("cta")
                    last_cta_ref[0] = now
                await asyncio.sleep(5)

        last_main_ref = [last_main]
        last_cta_ref = [last_cta]

        try:
            await asyncio.gather(
                _main_loop(),
                self._input_loop(),
                self._queue_worker(),
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self._running = False
            console.print("\n[dim]正在結束直播...[/]")
            await self._speak("closing")
            await self.la_session.stop()
            console.print(f"[bold]直播結束｜Credits: {self.total_credits:.2f}｜腳本數: {self.script_count}[/]")


async def main():
    parser = argparse.ArgumentParser(description="Virtual Streamer MVP")
    parser.add_argument("--avatar", required=True, help="Avatar name (e.g. katya)")
    parser.add_argument("--theme", required=True, help="直播主題")
    args = parser.parse_args()

    cfg = load_config()
    avatar = load_avatar(args.avatar)
    la_key, an_key, el_key = get_api_keys(cfg)

    session = StreamingSession(
        avatar=avatar,
        theme=args.theme,
        cfg=cfg,
        la_key=la_key,
        an_key=an_key,
        el_key=el_key,
    )

    try:
        await session.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
