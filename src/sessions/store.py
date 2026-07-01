from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from ..paths import SESSIONS_DIR

@dataclass
class StoredSession:
    """借鉴自 claw-code src/session_store.py，扩展了 messages 结构"""
    session_id: str
    messages: list[dict]          # 真实的 Anthropic message 格式
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = "claude-opus-4-6"

    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def estimated_cost_usd(self) -> float:
        # claude-opus-4-6: $15/M input, $75/M output（近似）
        return self.input_tokens / 1_000_000 * 15 + self.output_tokens / 1_000_000 * 75


SESSION_DIR = SESSIONS_DIR


def new_session_id() -> str:
    return uuid4().hex[:12]


def save_session(session: StoredSession, directory: Path | None = None) -> Path:
    target_dir = directory or SESSION_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{session.session_id}.json"
    path.write_text(json.dumps(asdict(session), indent=2, ensure_ascii=False))
    return path


def load_session(session_id: str, directory: Path | None = None) -> StoredSession:
    target_dir = directory or SESSION_DIR
    path = target_dir / f"{session_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Session not found: {session_id}")
    data = json.loads(path.read_text())
    return StoredSession(**data)


def list_sessions(directory: Path | None = None) -> list[StoredSession]:
    target_dir = directory or SESSION_DIR
    if not target_dir.exists():
        return []
    sessions = []
    for p in sorted(target_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text())
            sessions.append(StoredSession(**data))
        except Exception:
            continue
    return sessions
