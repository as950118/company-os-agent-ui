"""Reads task/doc status out of a scaffolded instance for the dashboard tab.

Purely a read-only scan of Markdown files `company-os-cli` already writes
(`tasks/*.md`, `projects/<slug>/{prd,architecture,api,adr}/*.md`,
`memory/decision-memory/ADR-*.md`) — no new file format is invented, and
nothing here writes to disk. Status values come straight from each doc's
`| Field | Value |` table, exactly as `docs/*-template.md` defines them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")

# docs/task-template.md's Status column, bucketed for the board's 3 columns.
_TASK_STATUS_GROUP = {
    "done": "done",
    "in review": "review",
    "qa": "review",
    "in progress": "todo",
    "ready": "todo",
    "backlog": "todo",
}
# PRD/Architecture use Draft/Approved; ADRs use Proposed/Accepted/Deprecated.
_REVIEW_STATUSES = {"draft", "proposed"}

_DOC_KINDS = ("prd", "architecture", "api", "adr")


@dataclass(frozen=True)
class TaskItem:
    id: str
    title: str
    status: str
    group: str
    type: str
    path: str


@dataclass(frozen=True)
class DocItem:
    kind: str
    id: str
    title: str
    status: str
    needs_review: bool
    path: str


def _parse_fields(text: str) -> dict[str, str]:
    """Reads the first `| Field | Value |` table in a doc into a dict."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = _TABLE_ROW_RE.match(line.strip())
        if not match:
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if len(cells) != 2:
            continue
        key, value = cells
        if not key or _SEPARATOR_CELL_RE.match(key) or _SEPARATOR_CELL_RE.match(value):
            continue
        if key == "Field" and value == "Value":
            continue
        fields.setdefault(key, value)
    return fields


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            return heading.split(":", 1)[1].strip() if ":" in heading else heading
    return fallback


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def scan_tasks(instance_dir: Path) -> list[TaskItem]:
    tasks_dir = instance_dir / "tasks"
    if not tasks_dir.is_dir():
        return []

    items = []
    for path in sorted(tasks_dir.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        text = _read_text(path)
        fields = _parse_fields(text)
        status = fields.get("Status", "").strip()
        items.append(
            TaskItem(
                id=fields.get("Task ID", "").strip() or path.stem,
                title=_title(text, path.stem),
                status=status or "Unknown",
                group=_TASK_STATUS_GROUP.get(status.lower(), "todo"),
                type=fields.get("Type", "").strip(),
                path=str(path.relative_to(instance_dir)),
            )
        )
    return items


def scan_docs(instance_dir: Path) -> list[DocItem]:
    candidates: list[tuple[str, Path]] = []

    projects_dir = instance_dir / "projects"
    if projects_dir.is_dir():
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith("_"):
                continue
            for kind in _DOC_KINDS:
                kind_dir = project_dir / kind
                if kind_dir.is_dir():
                    candidates.extend((kind, path) for path in sorted(kind_dir.glob("*.md")))

    decision_dir = instance_dir / "memory" / "decision-memory"
    if decision_dir.is_dir():
        candidates.extend(("adr", path) for path in sorted(decision_dir.glob("ADR-*.md")))

    items = []
    for kind, path in candidates:
        text = _read_text(path)
        fields = _parse_fields(text)
        status = fields.get("Status", "").strip()
        doc_id = next(
            (fields[k] for k in ("Doc ID", "PRD ID", "Spec ID") if k in fields),
            path.stem,
        )
        items.append(
            DocItem(
                kind=kind,
                id=doc_id,
                title=_title(text, path.stem),
                status=status or "—",
                needs_review=status.lower() in _REVIEW_STATUSES,
                path=str(path.relative_to(instance_dir)),
            )
        )
    return items


def build_board(instance_dir: Path) -> dict:
    tasks = scan_tasks(instance_dir)
    docs = scan_docs(instance_dir)
    docs_needing_review = [d for d in docs if d.needs_review]
    return {
        "tasks": {
            "done": [t.__dict__ for t in tasks if t.group == "done"],
            "todo": [t.__dict__ for t in tasks if t.group == "todo"],
            "review": [t.__dict__ for t in tasks if t.group == "review"],
        },
        "docs_needing_review": [d.__dict__ for d in docs_needing_review],
        "counts": {
            "tasks_total": len(tasks),
            "docs_total": len(docs),
            "docs_needing_review": len(docs_needing_review),
        },
    }


class DocReadError(Exception):
    """Raised when a requested doc path is outside the instance or not a readable Markdown file."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def read_doc(instance_dir: Path, rel_path: str) -> dict:
    """Returns one Markdown doc of the instance for the board's detail view.

    Only `.md` files that resolve inside `instance_dir` are served — the path
    comes from the browser, so `..`, absolute paths and symlinks pointing
    outside the instance are rejected.
    """
    if not rel_path or Path(rel_path).is_absolute():
        raise DocReadError("path must be relative to the instance directory.", 400)
    root = instance_dir.resolve()
    target = (root / rel_path).resolve()
    if target != root and root not in target.parents:
        raise DocReadError("path is outside the instance directory.", 400)
    if target.suffix.lower() != ".md":
        raise DocReadError("only Markdown (.md) documents can be viewed.", 400)
    if not target.is_file():
        raise DocReadError(f"{rel_path} does not exist.", 404)

    text = _read_text(target)
    return {
        "path": str(target.relative_to(root)),
        "title": _title(text, target.stem),
        "fields": _parse_fields(text),
        "markdown": text,
    }
