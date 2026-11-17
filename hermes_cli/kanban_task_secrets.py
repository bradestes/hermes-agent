"""Exact-task secret mapping for dispatcher-owned Kanban workers.

Values never enter config or the marker: config maps child variable names to
profile-scoped secret names, and the dispatcher resolves them only while the
assignee's secret scope is bound.
"""
from __future__ import annotations

import os
import re
from typing import Mapping

TASK_SECRET_ENV_MARKER = "_HERMES_KANBAN_TASK_SECRET_ENV"
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _validated_mapping(task_id: str, config: Mapping) -> dict[str, str]:
    kanban = config.get("kanban") if isinstance(config, Mapping) else None
    all_tasks = kanban.get("task_secret_env") if isinstance(kanban, Mapping) else None
    raw = all_tasks.get(task_id) if isinstance(all_tasks, Mapping) else None
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"kanban.task_secret_env.{task_id} must be a mapping")
    result: dict[str, str] = {}
    for target, source in raw.items():
        target, source = str(target).strip(), str(source).strip()
        if not _ENV_NAME.fullmatch(target) or not _ENV_NAME.fullmatch(source):
            raise ValueError(f"kanban task secret mapping for {task_id} contains an invalid environment name")
        if target == "BWS_ACCESS_TOKEN" or source == "BWS_ACCESS_TOKEN":
            raise ValueError("kanban task secret mappings may not expose BWS_ACCESS_TOKEN")
        if target.startswith("HERMES_") or target.startswith("_HERMES_"):
            raise ValueError("kanban task secret mappings may not override Hermes control variables")
        result[target] = source
    return result


def inject_task_secret_env(env: dict[str, str], task_id: str, config: Mapping) -> tuple[str, ...]:
    """Resolve and inject one task's configured values; return target names only."""
    mapping = _validated_mapping(task_id, config)
    if not mapping:
        return ()
    from agent.secret_scope import get_secret

    missing: list[str] = []
    for target, source in mapping.items():
        value = get_secret(source, "") or ""
        if not value:
            missing.append(source)
            continue
        env[target] = value
    if missing:
        for target in mapping:
            env.pop(target, None)
        raise RuntimeError(
            f"task {task_id} is missing configured profile secrets: {', '.join(sorted(missing))}")
    targets = tuple(sorted(mapping))
    env[TASK_SECRET_ENV_MARKER] = ",".join(targets)
    return targets


def restore_task_secret_env_for_child(env: dict[str, str]) -> dict[str, str]:
    """Re-add allowlisted values only for this dispatcher-owned worker's tool child.

    Delegate children fail ``owned_kanban_task`` and therefore receive none.
    The marker contains names only and is not copied into the child.
    """
    from agent.delegation_context import owned_kanban_task

    if not owned_kanban_task():
        return env
    raw = os.environ.get(TASK_SECRET_ENV_MARKER, "")
    for name in raw.split(","):
        name = name.strip()
        if (not _ENV_NAME.fullmatch(name) or name == "BWS_ACCESS_TOKEN"
                or name.startswith("HERMES_") or name.startswith("_HERMES_")):
            continue
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env
