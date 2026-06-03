from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Awaitable

# Callback type: async fn that receives a Comment
CommentCallback = Callable[["Comment"], Awaitable[None]]


@dataclass
class Comment:
    platform: str        # "youtube" | "tiktok" | "test"
    viewer_name: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        return f"[{self.platform}] {self.viewer_name}: {self.message}"
