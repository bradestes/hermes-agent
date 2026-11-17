import subprocess
from pathlib import Path

import pytest

from agent.delegation_context import delegated_child_context
from agent.secret_scope import reset_secret_scope, set_secret_scope
from hermes_cli.kanban_task_secrets import (
    TASK_SECRET_ENV_MARKER,
    inject_task_secret_env,
    restore_task_secret_env_for_child,
)


def test_exact_task_mapping_is_value_free_and_rejects_broad_token(monkeypatch):
    config = {"kanban": {"task_secret_env": {
        "t_allowed": {"TARGET_TOKEN": "SOURCE_TOKEN", "TARGET_ID": "SOURCE_ID"},
        "t_bad": {"BWS_ACCESS_TOKEN": "SOURCE_TOKEN"},
    }}}
    token = set_secret_scope({"SOURCE_TOKEN": "synthetic-token", "SOURCE_ID": "synthetic-id"})
    try:
        env = {"BWS_ACCESS_TOKEN": "must-not-cross"}
        assert inject_task_secret_env(env, "t_other", config) == ()
        assert "TARGET_TOKEN" not in env

        env = {}
        assert inject_task_secret_env(env, "t_allowed", config) == ("TARGET_ID", "TARGET_TOKEN")
        assert env["TARGET_TOKEN"] == "synthetic-token"
        assert env["TARGET_ID"] == "synthetic-id"
        assert env[TASK_SECRET_ENV_MARKER] == "TARGET_ID,TARGET_TOKEN"

        with pytest.raises(ValueError, match="BWS_ACCESS_TOKEN"):
            inject_task_secret_env({}, "t_bad", config)
    finally:
        reset_secret_scope(token)


def test_tool_child_restore_requires_dispatcher_ownership(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_allowed")
    monkeypatch.setenv(TASK_SECRET_ENV_MARKER, "TARGET_TOKEN,TARGET_ID,BWS_ACCESS_TOKEN")
    monkeypatch.setenv("TARGET_TOKEN", "synthetic-token")
    monkeypatch.setenv("TARGET_ID", "synthetic-id")
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "must-not-cross")

    restored = restore_task_secret_env_for_child({})
    assert restored == {"TARGET_TOKEN": "synthetic-token", "TARGET_ID": "synthetic-id"}

    with delegated_child_context("child"):
        assert restore_task_secret_env_for_child({}) == {}

    monkeypatch.delenv("HERMES_KANBAN_TASK")
    assert restore_task_secret_env_for_child({}) == {}


def test_worker_profile_scope_hydrates_external_secrets_before_build(monkeypatch, tmp_path):
    from agent.secret_scope import get_secret
    from hermes_cli import env_loader
    from hermes_cli import kanban_db_dispatch as kbd

    root = tmp_path / ".hermes"
    profile = root / "profiles" / "jonah"
    profile.mkdir(parents=True)
    root.joinpath("config.yaml").write_text("{}\n", encoding="utf-8")
    profile.joinpath("config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(root))

    hydrated = []

    def fake_hydrate(home):
        hydrated.append(Path(home))
        return {"CLOUDFLARE_ACCOUNT_ID_JONAH": "synthetic-account-id"}

    monkeypatch.setattr(env_loader, "hydrate_profile_secret_sources", fake_hydrate)
    monkeypatch.setattr(
        env_loader,
        "get_secret_source_values",
        lambda home: ({"CLOUDFLARE_ACCOUNT_ID_JONAH": "synthetic-account-id"} if hydrated else {}),
    )

    with kbd._worker_profile_scope(str(profile)):
        assert get_secret("CLOUDFLARE_ACCOUNT_ID_JONAH") == "synthetic-account-id"
    assert hydrated == [profile]


def test_default_spawn_maps_only_configured_task(monkeypatch, tmp_path):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_dispatch as kbd

    root = tmp_path / ".hermes"
    profile = root / "profiles" / "jonah"
    profile.mkdir(parents=True)
    root.joinpath("config.yaml").write_text("{}\n", encoding="utf-8")
    profile.joinpath("config.yaml").write_text(
        "kanban:\n"
        "  task_secret_env:\n"
        "    t_allowed:\n"
        "      TARGET_TOKEN: SOURCE_TOKEN\n"
        "      TARGET_ID: SOURCE_ID\n",
        encoding="utf-8",
    )
    profile.joinpath(".env").write_text(
        "SOURCE_TOKEN=synthetic-token\nSOURCE_ID=synthetic-id\nBWS_ACCESS_TOKEN=must-not-cross\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(kbd, "_resolve_hermes_argv", lambda: ["hermes"])
    monkeypatch.setattr(kbd, "_restart_safe_worker_argv", lambda task, cmd: cmd)
    monkeypatch.setattr("tools.process_registry.systemd_user_bus_env", lambda env: env)
    monkeypatch.setattr("tools.environments.local._prepend_hermes_bin_dir", lambda path: path)

    captured = []

    class Proc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        captured.append(dict(kwargs["env"]))
        return Proc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def task(task_id):
        return kb.Task(
            id=task_id, title="t", body=None, assignee="jonah", status="claimed", priority=0,
            created_by=None, created_at=0, started_at=None, completed_at=None,
            workspace_kind="dir", workspace_path=None, claim_lock=None, claim_expires=None,
            tenant=None,
        )

    assert kbd._default_spawn(task("t_allowed"), str(workspace)) == 4242
    allowed = captured.pop()
    assert allowed["TARGET_TOKEN"] == "synthetic-token"
    assert allowed["TARGET_ID"] == "synthetic-id"
    assert allowed[TASK_SECRET_ENV_MARKER] == "TARGET_ID,TARGET_TOKEN"
    assert "SOURCE_TOKEN" not in allowed
    assert "SOURCE_ID" not in allowed
    assert "BWS_ACCESS_TOKEN" not in allowed

    assert kbd._default_spawn(task("t_other"), str(workspace)) == 4242
    unrelated = captured.pop()
    assert "TARGET_TOKEN" not in unrelated
    assert "TARGET_ID" not in unrelated
    assert TASK_SECRET_ENV_MARKER not in unrelated
