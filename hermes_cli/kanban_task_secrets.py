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
    env[TASK_SECRET_ENV_MARKER] = ",".join(f"{target}={mapping[target]}" for target in targets)
    return targets


def restore_task_secret_env_for_child(env: dict[str, str]) -> dict[str, str]:
    """Re-add allowlisted values only for this dispatcher-owned worker's tool child.

    Delegate children fail ``owned_kanban_task`` and therefore receive none.
    The marker pins target/source names only and is not copied into the child.
    A worker startup may scrub a credential-shaped target after snapshotting its
    external sources, so a missing target is recovered from the pinned source
    inside the worker's own profile scope.
    """
    from agent.delegation_context import owned_kanban_task

    task_id = owned_kanban_task()
    if not task_id:
        return env
    from hermes_cli.config import load_config

    configured = _validated_mapping(task_id, load_config())
    raw = os.environ.get(TASK_SECRET_ENV_MARKER, "")
    pinned: list[tuple[str, str]] = []
    for item in raw.split(","):
        target, sep, source = item.strip().partition("=")
        if (not sep or not _ENV_NAME.fullmatch(target) or not _ENV_NAME.fullmatch(source)
                or target == "BWS_ACCESS_TOKEN" or source == "BWS_ACCESS_TOKEN"
                or target.startswith("HERMES_") or target.startswith("_HERMES_")
                or configured.get(target) != source):
            continue
        pinned.append((target, source))

    scope = None
    for target, source in pinned:
        value = os.environ.get(target)
        if value is None:
            if scope is None:
                from agent.secret_scope import build_profile_secret_scope
                from hermes_cli.env_loader import hydrate_profile_secret_sources
                from hermes_constants import get_hermes_home

                home = get_hermes_home()
                hydrate_profile_secret_sources(home)
                scope = build_profile_secret_scope(home)
            value = scope.get(source)
        if value is not None:
            env[target] = value
    return env
