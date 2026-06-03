"""
virtual-streamer MVP
Usage: python main.py --avatar katya --theme 測試直播
"""

import argparse
import asyncio
import os
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
    path = AVATARS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Avatar config not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


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

    async def _speak(self, trigger: str) -> None:
        text = await self.agent.get_script(trigger, self.context)
        self.script_count += 1

        voice_id = self.avatar.get("voice_id", "")
        try:
            if voice_id and self.el_key:
                pcm = await text_to_pcm(text, voice_id, self.el_key)
            else:
                # Fallback: macOS built-in TTS (for MVP testing without ElevenLabs)
                if not hasattr(self, "_warned_tts"):
                    self._warned_tts = True
                    console.print("[yellow]⚠ 使用 macOS TTS 作為備用（voice_id 或 ElevenLabs key 未設定）[/]")
                pcm = await text_to_pcm_macos(text)

            await self.la_session.send_audio(pcm)
            self.total_credits = self.la_session.credits_used
        except Exception as e:
            console.print(f"[red]TTS/Audio error: {e}[/]")

        self._print_status(trigger, text)
        self._log(trigger, text)

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

        console.print(Panel(
            f"[bold green]虛擬主播直播系統啟動[/]\n"
            f"Avatar: [cyan]{self.avatar['display_name']}[/]  "
            f"主題: [cyan]{self.theme}[/]\n"
            f"Avatar ID: {self.avatar['avatar_id']}",
            title="VIRTUAL STREAMER MVP",
            border_style="green",
        ))

        # Init LiveAvatar session
        try:
            console.print("[dim]建立 LiveAvatar session...[/]")
            await self.la_session.create()
            await self.la_session.start()
            await self.la_session.connect_ws()
            console.print("[green]✓ LiveAvatar 連線成功[/]")
        except Exception as e:
            console.print(f"[red]LiveAvatar 連線失敗: {e}[/]")
            console.print("[yellow]繼續執行（無虛擬人渲染）...[/]")

        # Opening
        await self._speak("opening")

        last_main = time.time()
        last_cta = time.time()
        session_start = time.time()

        try:
            while self._running:
                now = time.time()

                # Auto-renew session every 55 minutes
                if (now - session_start) / 60 >= max_min:
                    await self._renew_session()
                    session_start = now

                # Main content every N seconds
                if now - last_main >= main_interval:
                    await self._speak("main")
                    last_main = now

                # CTA every X seconds
                if now - last_cta >= cta_interval:
                    await self._speak("cta")
                    last_cta = now

                await asyncio.sleep(5)

        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self._speak("closing")
            await self.la_session.stop()
            console.print(f"\n[bold]直播結束｜總 Credits: {self.total_credits}｜腳本數: {self.script_count}[/]")


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
