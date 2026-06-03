"""
Comment Handler
───────────────
流程：
  ① 收到 Comment（platform + viewer_name + message）
  ② 比對 scripts/{avatar}/triggers.yaml 關鍵字
  ③ 符合  → 套用對應腳本模板，填入觀眾名 / 主題
  ④ 不符合 → 呼叫 Claude API 即時生成 ≤20 字回應
  ⑤ 組裝成：「{viewer_name} 說：{response}」
  ⑥ 回傳完整台詞字串（由 StreamingSession._speak_queue 接手送 TTS）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    import anthropic
    from listeners.base import Comment


CLAUDE_SYSTEM = (
    "你是一個活潑的虛擬直播主。"
    "觀眾剛剛傳來了一則留言，請你用親切簡短的話回應，"
    "限制在20個字以內，不要加標點符號以外的多餘說明。"
    "直接回應內容，不要說「我的回應：」這類前綴。"
)


class CommentHandler:
    def __init__(
        self,
        avatar_name: str,
        scripts_base: Path,
        claude_client: "anthropic.Anthropic | None" = None,
        claude_model: str = "claude-haiku-4-5",
    ):
        self.avatar_name  = avatar_name
        self.claude       = claude_client
        self.claude_model = claude_model
        self.triggers: list[dict] = []
        self._load_triggers(scripts_base / avatar_name / "triggers.yaml")

    # ──────────────────────────────────────────────
    # Public
    # ──────────────────────────────────────────────

    async def handle(self, comment: "Comment", context: dict) -> str:
        """
        Returns the full sentence Katya should speak.
        Format: "{viewer_name} 說：{response}"
        """
        response = self._match_trigger(comment.message, comment.viewer_name, context)
        if response is None:
            response = await self._generate(comment.message, comment.viewer_name, context)
        return f"{comment.viewer_name} 說：{response}"

    # ──────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────

    def _load_triggers(self, path: Path) -> None:
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self.triggers = data.get("triggers", [])

    def _match_trigger(
        self, message: str, viewer_name: str, context: dict
    ) -> str | None:
        """Return formatted response if any keyword matches, else None."""
        lower_msg = message.lower()
        for trig in self.triggers:
            keywords = trig.get("keywords", [])
            if any(kw.lower() in lower_msg for kw in keywords):
                template: str = trig.get("response", "")
                try:
                    return template.format(
                        viewer_name=viewer_name,
                        theme=context.get("theme", ""),
                    )
                except KeyError:
                    return template
        return None

    async def _generate(
        self, message: str, viewer_name: str, context: dict
    ) -> str:
        """Claude API fallback — ≤20 Chinese characters."""
        if self.claude is None:
            return "謝謝你的留言！"

        prompt = (
            f"觀眾「{viewer_name}」說：「{message}」\n"
            f"當前直播主題：{context.get('theme', '直播')}\n"
            "請回應（≤20字）："
        )
        try:
            msg = self.claude.messages.create(
                model=self.claude_model,
                max_tokens=60,
                system=CLAUDE_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            reply = msg.content[0].text.strip()
            # Hard-trim to 20 chars just in case
            return reply[:20]
        except Exception:
            return "謝謝！"
