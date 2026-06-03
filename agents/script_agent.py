import os
import random
import glob
import asyncio
import anthropic
from pathlib import Path


TRIGGER_DIR_MAP = {
    "opening":    "01_opening",
    "main":       "02_main",
    "new_viewer": "03_interaction",
    "transition": "04_transition",
    "cta":        "05_cta",
    "closing":    "06_closing",
}


class ScriptAgent:
    def __init__(self, avatar_config: dict, claude_api_key: str, claude_model: str):
        self.avatar = avatar_config
        self.script_base = Path(__file__).parent.parent / "scripts" / avatar_config["script_dir"]
        self.persona = avatar_config["persona"]
        self.client = anthropic.Anthropic(api_key=claude_api_key)
        self.model = claude_model
        self._used_scripts: set[str] = set()

    def _load_scripts(self, trigger: str) -> list[str]:
        folder = TRIGGER_DIR_MAP.get(trigger, "02_main")
        pattern = str(self.script_base / folder / "*.txt")
        return glob.glob(pattern)

    def _pick_script(self, files: list[str]) -> str | None:
        unused = [f for f in files if f not in self._used_scripts]
        if not unused:
            self._used_scripts.clear()
            unused = files
        if not unused:
            return None
        chosen = random.choice(unused)
        self._used_scripts.add(chosen)
        return chosen

    def _fill_vars(self, text: str, context: dict) -> str:
        return text.format_map({**context, **{"theme": context.get("theme", ""), "viewer_name": context.get("viewer_name", "大家")}})

    async def get_script(self, trigger: str, context: dict) -> str:
        files = self._load_scripts(trigger)
        chosen = self._pick_script(files)

        if chosen:
            raw = Path(chosen).read_text(encoding="utf-8").strip()
            return self._fill_vars(raw, context)

        # Fallback: generate via Claude
        return await self._generate(trigger, context)

    async def _generate(self, trigger: str, context: dict) -> str:
        theme = context.get("theme", "直播")
        viewer = context.get("viewer_name", "")

        prompt_map = {
            "opening":    f"請用角色說一段直播開場白，主題是「{theme}」。",
            "main":       f"請用角色說一段關於「{theme}」的內容分享，約60-100字。",
            "new_viewer": f"請用角色歡迎新觀眾「{viewer}」加入，並帶入主題「{theme}」。",
            "transition": f"請用角色說一段轉場詞，從一個話題過渡到下一個，主題是「{theme}」。",
            "cta":        "請用角色說一段追蹤/互動呼籲，約30字。",
            "closing":    f"請用角色說一段結尾收播詞，主題是「{theme}」。",
        }
        user_prompt = prompt_map.get(trigger, f"請用角色說一段關於「{theme}」的話，約60字。")

        message = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            system=self.persona.format(**context),
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text.strip()
