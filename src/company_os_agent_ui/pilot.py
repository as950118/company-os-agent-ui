"""Real PM -> Architect -> Backend agent pilot against a scaffolded instance.

Runs against a directory produced by `company-os init` (from the separate
`company-os-cli` package), reusing its own `agents/*.yaml` system prompts,
`roles/*.md` role docs, and `docs/{prd,architecture}-template.md` output
formats — no separate prompt format is invented here. Exactly 3 real LLM
calls per run (PM, Architect, Backend); handoff "meeting" dialogue is
deterministic, not LLM-generated.

LLM output is never used to choose a file path — only the 3 fixed paths in
_OUTPUT_PATHS are ever written, so nothing the model says can redirect a
write elsewhere on disk.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import httpx
import yaml
from dotenv import dotenv_values

from company_os_cli.scaffold import MANIFEST_FILENAME

ROLE_ORDER = ("pm", "architect", "backend")
_MAX_FEATURE_REQUEST_CHARS = 4000
_MAX_TOKENS = 1200
_RUN_TIMEOUT_SECONDS = 300

_OUTPUT_PATHS = {
    "pm": ("prd", "PRD-0000-pilot.md"),
    "architect": ("architecture", "ARCH-0000-pilot.md"),
    "backend": ("api", "IMPLEMENTATION-NOTES-0000-pilot.md"),
}
_OUTPUT_TITLES = {
    "pm": "PRD (pilot)",
    "architect": "Architecture (pilot)",
    "backend": "Implementation Notes (pilot)",
}

Emit = Callable[[dict], Awaitable[None]]


class PilotError(RuntimeError):
    """Safe to show verbatim to the client."""


@dataclass(frozen=True)
class RunConfig:
    mock: bool
    api_key: str
    model: str
    base_url: str


# A plain boolean guard is enough here: the check-then-set below never
# awaits between reading and flipping `_run_active`, and asyncio only
# switches coroutines at an `await` point on its single-threaded event loop —
# so no other request can interleave between the check and the set. Do NOT
# turn this into an `asyncio.Lock()` used with `async with`: that would queue
# a second run instead of rejecting it outright, defeating the point (a
# local, single-user tool should never silently double-spend on LLM calls).
_run_active = False


def read_run_config(instance_dir: Path) -> RunConfig:
    """Process env > <instance>/runtime/.env > defaults — same precedence
    `runtime/company_os/config.py`'s `mock_llm()` gets for free from
    `load_dotenv()` (which never overrides an already-set process env var).
    """
    file_values = dotenv_values(instance_dir / "runtime" / ".env")

    def get(key: str, default: str) -> str:
        return os.environ.get(key) or file_values.get(key) or default

    mock_flag = get("MOCK_LLM", "true").strip().lower()
    return RunConfig(
        mock=mock_flag in {"1", "true", "yes"},
        api_key=get("OPENROUTER_API_KEY", "").strip(),
        model=get("OPENROUTER_MODEL", "openrouter/free").strip(),
        base_url=get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip(),
    )


def load_role_prompt(instance_dir: Path, role: str) -> str:
    yaml_path = instance_dir / "agents" / f"{role}.yaml"
    role_doc_path = instance_dir / "roles" / f"{role}.md"
    if not yaml_path.is_file():
        raise PilotError(f"Missing agent definition: agents/{role}.yaml")
    if not role_doc_path.is_file():
        raise PilotError(f"Missing role doc: roles/{role}.md")

    agent_def = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    system_prompt = str(agent_def.get("system_prompt", "")).strip()
    role_doc = role_doc_path.read_text(encoding="utf-8").strip()
    return f"{system_prompt}\n\n{role_doc}"


def _read_required(path: Path, relpath: str) -> str:
    if not path.is_file():
        raise PilotError(f"Missing required file: {relpath}")
    return path.read_text(encoding="utf-8")


def _slug(instance_dir: Path) -> str:
    manifest_path = instance_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise PilotError(
            f"{instance_dir} has no {MANIFEST_FILENAME} — run `company-os init` first."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        slug = manifest["mapping"]["PRODUCT_SLUG"]
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        raise PilotError(f"Could not read PRODUCT_SLUG from {manifest_path}") from exc
    return slug


def _mock_response(role: str, user_prompt: str) -> str:
    excerpt = user_prompt.strip().splitlines()[0][:120] if user_prompt.strip() else ""
    return (
        f"[MOCK OUTPUT — {role}]\n\n"
        f"MOCK_LLM=true, so this is canned text, not a real model response.\n"
        f"Prompt excerpt: {excerpt}\n\n"
        "Set MOCK_LLM=false and OPENROUTER_API_KEY in runtime/.env to call OpenRouter for real."
    )


async def call_llm(cfg: RunConfig, *, role: str, system_prompt: str, user_prompt: str) -> str:
    if cfg.mock:
        return _mock_response(role, user_prompt)

    if not cfg.api_key:
        raise PilotError(
            "MOCK_LLM=false but OPENROUTER_API_KEY is not set in runtime/.env."
        )

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{cfg.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json={
                    "model": cfg.model,
                    "max_tokens": _MAX_TOKENS,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
    except httpx.HTTPError as exc:
        raise PilotError(f"Could not reach OpenRouter: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise PilotError(f"OpenRouter returned a non-JSON response (HTTP {response.status_code})") from exc

    if response.is_error or "error" in data:
        message = (data.get("error") or {}).get("message") or response.text[:300]
        raise PilotError(f"OpenRouter error (HTTP {response.status_code}): {message}")

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PilotError("OpenRouter response did not include a message choice.") from exc


def _handoff_dialogue(from_role: str, to_role: str, artifact_title: str) -> list[dict]:
    return [
        {"speaker": from_role, "text": f"{artifact_title} 작성을 마쳤습니다. {to_role}님께 전달합니다."},
        {"speaker": to_role, "text": f"확인했습니다. {artifact_title} 기준으로 이어서 진행하겠습니다."},
    ]


async def _run_stage(
    *,
    instance_dir: Path,
    slug: str,
    cfg: RunConfig,
    role: str,
    user_prompt: str,
    emit: Emit,
) -> str:
    stage, filename = _OUTPUT_PATHS[role]
    await emit({"type": "stage_started", "role": role, "stage": stage})

    system_prompt = load_role_prompt(instance_dir, role)
    content = await call_llm(cfg, role=role, system_prompt=system_prompt, user_prompt=user_prompt)

    out_path = instance_dir / "projects" / slug / stage / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")

    await emit(
        {
            "type": "stage_output",
            "role": role,
            "stage": stage,
            "title": _OUTPUT_TITLES[role],
            "content": content,
            "path": str(out_path.relative_to(instance_dir)),
        }
    )
    return content


async def _run_meeting(*, from_role: str, to_role: str, topic: str, artifact_title: str, emit: Emit) -> None:
    await emit(
        {
            "type": "meeting_started",
            "topic": topic,
            "participants": [from_role, to_role],
            "dialogue": _handoff_dialogue(from_role, to_role, artifact_title),
        }
    )
    await asyncio.sleep(1.5)
    await emit({"type": "meeting_ended", "participants": [from_role, to_role]})


async def _run_pilot_body(instance_dir: Path, feature_request: str, emit: Emit) -> None:
    slug = _slug(instance_dir)
    cfg = read_run_config(instance_dir)

    await emit(
        {
            "type": "run_started",
            "feature_request": feature_request,
            "instance": str(instance_dir),
            "mock": cfg.mock,
        }
    )

    prd_template = _read_required(instance_dir / "docs" / "prd-template.md", "docs/prd-template.md")
    prd = await _run_stage(
        instance_dir=instance_dir,
        slug=slug,
        cfg=cfg,
        role="pm",
        user_prompt=(
            f"Feature request:\n{feature_request}\n\n"
            f"Write a PRD by filling out every section of this template "
            f"(keep the headings):\n\n{prd_template}"
        ),
        emit=emit,
    )

    await _run_meeting(
        from_role="pm", to_role="architect", topic="PRD 핸드오프", artifact_title="PRD", emit=emit
    )

    architecture_template = _read_required(
        instance_dir / "docs" / "architecture-template.md", "docs/architecture-template.md"
    )
    architecture = await _run_stage(
        instance_dir=instance_dir,
        slug=slug,
        cfg=cfg,
        role="architect",
        user_prompt=(
            f"Feature request:\n{feature_request}\n\n"
            f"PRD:\n{prd}\n\n"
            f"Write an architecture doc by filling out every section of this "
            f"template (keep the headings), referencing the PRD's AC "
            f"numbers where relevant:\n\n{architecture_template}"
        ),
        emit=emit,
    )

    await _run_meeting(
        from_role="architect",
        to_role="backend",
        topic="설계 핸드오프",
        artifact_title="Architecture",
        emit=emit,
    )

    await _run_stage(
        instance_dir=instance_dir,
        slug=slug,
        cfg=cfg,
        role="backend",
        user_prompt=(
            f"Feature request:\n{feature_request}\n\n"
            f"Architecture:\n{architecture}\n\n"
            "Write implementation notes for this: key API endpoints, "
            "modules/functions, and a brief pseudocode sketch. This is a "
            "design pilot — describe the plan, do not claim to have "
            "created any files; only the orchestrator writes files, at "
            "fixed paths it controls."
        ),
        emit=emit,
    )

    await emit(
        {
            "type": "run_completed",
            "outputs": [
                {"role": role, "stage": _OUTPUT_PATHS[role][0], "path": f"projects/{slug}/{_OUTPUT_PATHS[role][0]}/{_OUTPUT_PATHS[role][1]}"}
                for role in ROLE_ORDER
            ],
        }
    )


async def run_pilot(instance_dir: Path, feature_request: str, emit: Emit) -> None:
    """Raises PilotError (bad input/config) or asyncio.TimeoutError on failure —
    it does not catch its own errors into `error` events. Callers driving a
    live channel (the /ws/run handler) are expected to catch around this call
    and turn any exception into a final `error` frame themselves, so that
    logging/message-sanitization stays a transport concern, not a pilot one.
    """
    global _run_active

    feature_request = feature_request.strip()
    if not feature_request:
        raise PilotError("Feature request must not be empty.")
    if len(feature_request) > _MAX_FEATURE_REQUEST_CHARS:
        raise PilotError(f"Feature request too long (max {_MAX_FEATURE_REQUEST_CHARS} chars).")

    if _run_active:
        raise PilotError("A pilot run is already in progress. Wait for it to finish.")
    _run_active = True
    try:
        await asyncio.wait_for(
            _run_pilot_body(instance_dir, feature_request, emit), timeout=_RUN_TIMEOUT_SECONDS
        )
    finally:
        _run_active = False
