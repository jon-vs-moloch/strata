#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = ROOT / ".knowledge"
SPEC_DIR = ROOT / "docs" / "spec"
CURRENT_KB_PATH = SPEC_DIR / "current_knowledge_base.md"
PROVENANCE_PATH = KNOWLEDGE_DIR / "provenance_index.json"


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


def build_knowledge_snapshot() -> Dict[str, object]:
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted([path for path in KNOWLEDGE_DIR.glob("*.md") if path.is_file()])
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    provenance: List[Dict[str, str]] = []
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

    CURRENT_KB_PATH.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    PROVENANCE_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "files": provenance,
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
        "current_kb_path": str(CURRENT_KB_PATH.relative_to(ROOT)),
        "provenance_path": str(PROVENANCE_PATH.relative_to(ROOT)),
        "sections": {kind: len(entries) for kind, entries in grouped.items()},
    }


def main() -> None:
    print(json.dumps(build_knowledge_snapshot(), indent=2))


if __name__ == "__main__":
    main()
