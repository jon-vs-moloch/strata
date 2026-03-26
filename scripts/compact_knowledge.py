#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = ROOT / ".knowledge"
SPEC_DIR = ROOT / "docs" / "spec"
CURRENT_KB_PATH = SPEC_DIR / "current_knowledge_base.md"
PROVENANCE_PATH = KNOWLEDGE_DIR / "provenance_index.json"
ARCHIVE_DIR = KNOWLEDGE_DIR / "archive"
MAX_HOT_FINAL = 8
MAX_HOT_WIP = 12


def _classify(path: Path) -> str:
    name = path.name
    if name.startswith("final_research_"):
        return "final_research"
    if name.startswith("wip_research_"):
        return "wip_research"
    if "performance" in name:
        return "telemetry"
    return "misc"


def _snippet(text: str, limit: int = 800) -> str:
    cleaned = " ".join(text.strip().split())
    return cleaned[:limit]


def _archive_plan(files: List[Path]) -> tuple[List[Path], List[Path]]:
    final_files = [path for path in files if _classify(path) == "final_research"]
    wip_files = [path for path in files if _classify(path) == "wip_research"]
    keep = set(final_files[-MAX_HOT_FINAL:] + wip_files[-MAX_HOT_WIP:])
    archived = [path for path in files if _classify(path) in {"final_research", "wip_research"} and path not in keep]
    hot = [path for path in files if path not in archived]
    return hot, archived


def _archive_file(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    target_dir = ARCHIVE_DIR / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if not target.exists():
        shutil.move(str(path), str(target))
    return target


def build_knowledge_snapshot() -> Dict[str, object]:
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted([path for path in KNOWLEDGE_DIR.glob("*.md") if path.is_file()])
    files, archived_candidates = _archive_plan(files)
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    provenance: List[Dict[str, str]] = []
    archived_entries: List[Dict[str, str]] = []
    for path in archived_candidates:
        text = path.read_text(encoding="utf-8", errors="ignore")
        target = _archive_file(path)
        archived_entries.append(
            {
                "filename": target.name,
                "kind": _classify(target),
                "archived_path": str(target.relative_to(ROOT)),
                "snippet": _snippet(text),
            }
        )
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        kind = _classify(path)
        entry = {
            "filename": path.name,
            "kind": kind,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "snippet": _snippet(text),
        }
        grouped[kind].append(entry)
        provenance.append(entry)

    lines = [
        "# Current Knowledge Base",
        "",
        "This is the compacted, current-facing knowledge view for Strata.",
        "Raw `.knowledge/` notes remain the archival provenance layer.",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Sections",
    ]
    for kind, entries in sorted(grouped.items()):
        lines.append(f"### {kind.replace('_', ' ').title()}")
        for entry in entries[-12:]:
            lines.append(f"- **{entry['filename']}**: {entry['snippet']}")
        lines.append("")
    if archived_entries:
        lines.extend(
            [
                "## Archived Research",
                "",
                f"- {len(archived_entries)} older research notes were archived after synthesis to avoid unbounded hot-note growth.",
                "",
            ]
        )

    CURRENT_KB_PATH.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    PROVENANCE_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "hot_files": provenance,
                "archived_files": archived_entries,
                "current_kb_path": str(CURRENT_KB_PATH.relative_to(ROOT)),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(provenance),
        "archived_file_count": len(archived_entries),
        "current_kb_path": str(CURRENT_KB_PATH.relative_to(ROOT)),
        "provenance_path": str(PROVENANCE_PATH.relative_to(ROOT)),
        "sections": {kind: len(entries) for kind, entries in grouped.items()},
    }


def main() -> None:
    print(json.dumps(build_knowledge_snapshot(), indent=2))


if __name__ == "__main__":
    main()
