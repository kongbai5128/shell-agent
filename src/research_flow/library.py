from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import RESEARCH_DIR, ensure_using_dirs


def paper_id_from_name(name: str) -> str:
    """根据论文文件名生成稳定、适合做目录名的论文 ID。"""
    stem = Path(name).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem or "paper"


@dataclass
class ResearchLibrary:
    """用于恢复后 research 流程的小型 JSON 索引。"""

    index_path: Path = field(default_factory=lambda: RESEARCH_DIR / "index.json")

    def __post_init__(self) -> None:
        """初始化索引对象时确保 using 目录结构存在。"""
        ensure_using_dirs()

    def load(self) -> dict[str, Any]:
        """读取 research 索引；文件不存在或损坏时返回空索引。"""
        if not self.index_path.exists():
            return {"papers": {}}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {"papers": {}}
        if not isinstance(data, dict):
            return {"papers": {}}
        data.setdefault("papers", {})
        return data

    def save(self, data: dict[str, Any]) -> None:
        """将 research 索引写回 using/research/index.json。"""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def upsert_paper(self, paper_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """新增或更新论文记录，并刷新更新时间。"""
        data = self.load()
        papers = data.setdefault("papers", {})
        current = dict(papers.get(paper_id, {}))
        current.update(metadata)
        current["paper_id"] = paper_id
        current["updated_at"] = int(time.time())
        papers[paper_id] = current
        self.save(data)
        return current

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        """按论文 ID 读取单条论文记录。"""
        paper = self.load().get("papers", {}).get(paper_id)
        return paper if isinstance(paper, dict) else None

    def search(self, query: str) -> list[dict[str, Any]]:
        """在索引元数据中做轻量字符串检索。"""
        query_l = query.lower()
        results = []
        for paper_id, metadata in self.load().get("papers", {}).items():
            blob = json.dumps(metadata, ensure_ascii=False).lower()
            if query_l in paper_id.lower() or query_l in blob:
                results.append(metadata)
        return results
