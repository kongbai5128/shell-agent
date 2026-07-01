from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import RESEARCH_DIR, ensure_using_dirs


def paper_id_from_name(name: str) -> str:
    stem = Path(name).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem or "paper"


@dataclass
class ResearchLibrary:
    """Small JSON index for the reconstructed research workflow."""

    index_path: Path = field(default_factory=lambda: RESEARCH_DIR / "index.json")

    def __post_init__(self) -> None:
        ensure_using_dirs()

    def load(self) -> dict[str, Any]:
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
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def upsert_paper(self, paper_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
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
        paper = self.load().get("papers", {}).get(paper_id)
        return paper if isinstance(paper, dict) else None

    def search(self, query: str) -> list[dict[str, Any]]:
        query_l = query.lower()
        results = []
        for paper_id, metadata in self.load().get("papers", {}).items():
            blob = json.dumps(metadata, ensure_ascii=False).lower()
            if query_l in paper_id.lower() or query_l in blob:
                results.append(metadata)
        return results

