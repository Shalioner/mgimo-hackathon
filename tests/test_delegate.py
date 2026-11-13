"""Делегат не наследует привязку к сеансу родительской оболочки (Orca и подобные).

Иначе дочерний `codex exec` шлёт события хуков от имени воркера, и оболочка
считает, что воркер закончил работу (так останавливалась ночная репетиция T05).
"""
import delegate as D


def test_child_env_drops_parent_session_vars():
    parent = {
        "ORCA_PANE_KEY": "pane", "ORCA_AGENT_HOOK_PORT": "1", "ORCA_AGENT_HOOK_TOKEN": "t",
        "PATH": "/usr/bin", "HOME": "/home/u", "OPENAI_API_KEY": "k", "CODEX_HOME": "/c",
    }
    env = D.child_env(parent)
    assert not [k for k in env if k.startswith("ORCA_")]
    # всё остальное (путь, домашняя папка, доступы самой модели) сохраняется
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/u"
    assert env["OPENAI_API_KEY"] == "k" and env["CODEX_HOME"] == "/c"


def test_child_env_does_not_touch_parent(monkeypatch):
    monkeypatch.setenv("ORCA_PANE_KEY", "pane")
    D.child_env()
    import os
    assert os.environ["ORCA_PANE_KEY"] == "pane"
