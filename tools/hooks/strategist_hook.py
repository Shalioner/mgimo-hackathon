#!/usr/bin/env python3
"""Хуки агента «Стратег» — один скрипт для Claude Code и Codex (формат событий у них совместим).

  session-start  → печатает `ledger.py status`: агент сразу видит проекты, текущий шаг и то, что ждёт решения.
  after-write    → если инструмент записал файл в projects/<case>/state/ledger/, запускает validate для этого проекта.
                   Есть ошибки → код 2 и текст в stderr: среда возвращает его агенту как обратную связь.
  user-prompt    → если сообщение человека похоже на возражение по существу («это не так», «ты не понял»,
                   «перечитай», раздражение, мат на 13 языках), добавляет агенту одну строку: начать с skills/self-audit.
                   Только регулярные выражения, без модели и без сети; решает по-прежнему агент.
                   Повторное возражение за 30 минут в той же сессии — подсказка жёстче: 2-е — проверить прошлый
                   самоаудит, 3-е — остановить правки до подтверждения человека и предложить эскалацию (AGENTS §6).

Хук никогда не ломает работу: при любой собственной ошибке выходит с кодом 0.
"""
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import objection_lexicon as LEX  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = os.name == "nt"
PY = ROOT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
LEDGER = ROOT / "tools" / "ledger.py"
VENV_HINT = (r"python -m venv .venv && .venv\Scripts\pip install -r requirements.txt" if WINDOWS
             else "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
# Путь к реестру в событии: прямые или обратные слэши (Windows), в т. ч. экранированные в JSON.
LEDGER_PATH = re.compile(r"projects[\\/]+([A-Za-z0-9_.\-]+)[\\/]+state[\\/]+ledger[\\/]")


def child(argv, timeout):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)


def run(args, timeout=60):
    py = str(PY) if PY.exists() else sys.executable
    return child([py, str(LEDGER), *args], timeout)


def session_start() -> int:
    if not PY.exists():
        print(f"Стратег: окружение не создано — выполните `{VENV_HINT}` (AGENTS.md §1).")
        return 0
    e = run(["env"])
    r = run(["status"])
    py = str(PY) if PY.exists() else sys.executable
    try:
        guide = child([py, str(ROOT / 'tools/doctor.py'), 'welcome', '--once'], 5)
        if guide.returncode == 0 and guide.stdout.strip():
            print('Первое знакомство: покажи пользователю этот короткий гайд один раз. '
                  'Это приглашение к работе, а не подтверждение работоспособности всех интеграций.\n' + guide.stdout.strip())
    except subprocess.SubprocessError:
        pass
    try:
        m = child([py, str(ROOT / "tools" / "models.py"), "check", "--brief"], 40)
        if m.stdout.strip():
            print(m.stdout.strip())
    except subprocess.SubprocessError:
        pass
    print((e.stdout.strip() + "\n") if e.stdout else "")
    print("Стратег — состояние проектов (ledger.py status):\n" + (r.stdout.strip() or "проектов нет"))
    return 0


def after_write(payload: str) -> int:
    projects = sorted(set(LEDGER_PATH.findall(payload)))
    if not projects or not PY.exists():
        return 0
    failed = []
    for slug in projects:
        proj = ROOT / "projects" / slug
        if not proj.is_dir():
            continue
        r = run(["validate", str(proj)])
        if r.returncode != 0:
            errs = [l for l in r.stdout.splitlines() if l.startswith("[ОШИБКА]")]
            failed.append(f"projects/{slug}: " + "; ".join(errs[:8]))
    if failed:
        print("Реестр не проходит validate после записи — исправьте до следующего шага:\n" + "\n".join(failed),
              file=sys.stderr)
        return 2
    return 0


# --- возражение человека → skills/self-audit -------------------------------------------------------
# Словарь и нормализация — tools/hooks/objection_lexicon.py (13 языков, маскировка, исключения).
QUOTED = re.compile(r"```.*?```|`[^`\n]*`|«[^»]*»|\"[^\"\n]*\"|^\s*>.*$", re.DOTALL | re.MULTILINE)
SELF_AUDIT_HINT = ("Стратег: похоже на возражение по существу ({labels}). Если человек оспаривает вывод — сначала "
                   "`skills/self-audit`: найди в реестре сломанное основание (`ledger.py trace`), не переписывай текст "
                   "и не поднимай уровень модели. Если это правка стиля, вопрос или шутка — просто ответь.")


def prompt_text(payload: str) -> str:
    """Текст сообщения человека из события UserPromptSubmit (Claude Code и Codex: поле prompt)."""
    try:
        data = json.loads(payload)
    except ValueError:
        return payload
    if isinstance(data, dict):
        for key in ("prompt", "user_prompt", "message", "text"):
            if isinstance(data.get(key), str):
                return data[key]
    return ""


def detect_objection(text: str):
    """Метки найденных признаков возражения; цитаты, код и вставки (>…) не считаются словами человека."""
    return LEX.detect(QUOTED.sub(" ", (text or "")[:4000]))


# --- повторные возражения: нарастание подсказки (идея лестницы уровней Z.A.E.B.A.L., код свой) -------------
# Счёт — по session_id события, в окне 30 минут, во временной папке системы (не в репозитории).
# Вес: возражение по существу — 1, только мат без других признаков — 0,5. Благодарность/согласие сбрасывает счёт.
WINDOW_SEC = 30 * 60
ACK = re.compile(r"^\W*(?:ок|окей|ok|okay|спасибо|благодарю|отлично|супер|принято|да,?\s+(?:верно|так|теперь\s+так)"
                 r"|теперь\s+(?:верно|правильно)|thanks|thank\s+you|great|perfect)\b", re.IGNORECASE)
REPEAT_HINT = (" Это повторное возражение за последние 30 минут: сначала найди в `run_log.md` прошлый самоаудит "
               "и проверь, что он на деле изменил; не повторяй прежний вывод другими словами. Если аудит уже был, "
               "а человек снова отклоняет результат, — эскалация по AGENTS §6 (`ledger.py route … --failed`).")
STOP_HINT = ("Стратег: уже {n}-е возражение за 30 минут ({labels}). Если это спор по существу — остановись: не правь "
             "файлы, пока человек явно не подтвердит продолжение. Покажи 3–5 строк: что уже пробовал, какая опора "
             "сломана, что не проверено, — и предложи эскалацию по AGENTS §6 (`ledger.py route … --failed`). "
             "Если это не спор — просто ответь.")


def state_file(session_id: str) -> Path:
    import hashlib
    import tempfile
    base = Path(os.environ.get("STRATEGIST_HOOK_STATE") or Path(tempfile.gettempdir()) / "strategist-hook")
    return base / (hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24] + ".json")


def session_id(payload: str) -> str:
    try:
        data = json.loads(payload)
    except ValueError:
        return ""
    sid = data.get("session_id") or data.get("sessionId") if isinstance(data, dict) else ""
    return sid if isinstance(sid, str) else ""


def streak(sid: str, weight: float, reset: bool = False, now: float = None) -> float:
    """Сумма весов возражений сессии в окне (вместе с текущим). Любая ошибка — как будто истории нет."""
    import time
    now = time.time() if now is None else now
    if not sid:
        return weight
    f = state_file(sid)
    try:
        hist = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        hist = [h for h in hist if isinstance(h, list) and len(h) == 2 and now - float(h[0]) < WINDOW_SEC]
    except (OSError, ValueError, TypeError):
        hist = []
    if reset:
        hist = []
    if weight:
        hist.append([now, weight])
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(hist), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass
    return sum(float(h[1]) for h in hist)


def user_prompt(payload: str) -> int:
    text = prompt_text(payload)
    labels = detect_objection(text)
    sid = session_id(payload)
    if not labels:
        if sid and ACK.search(text or ""):
            streak(sid, 0, reset=True)  # «спасибо, теперь верно» — серия закончилась
        return 0
    weight = 0.5 if set(labels) == {"мат"} else 1.0
    total = streak(sid, weight)
    shown = ", ".join(dict.fromkeys(labels))
    if total >= 3:
        print(STOP_HINT.format(n=int(total) if total == int(total) else total, labels=shown))
    elif total >= 2:
        print(SELF_AUDIT_HINT.format(labels=shown) + REPEAT_HINT)
    else:
        print(SELF_AUDIT_HINT.format(labels=shown))
    return 0


def read_stdin(timeout: float = 2.0) -> str:
    """Читает JSON события, но не дольше timeout: незакрытый stdin не должен вешать сессию."""
    try:
        if sys.stdin.isatty():
            return ""
        if WINDOWS:  # select() на Windows не работает с пайпами — читаем в фоновом потоке
            box = []
            t = threading.Thread(target=lambda: box.append(sys.stdin.buffer.read()), daemon=True)
            t.start()
            t.join(timeout)
            return box[0].decode("utf-8", errors="replace") if box else ""
        import select
        fd = sys.stdin.fileno()
        chunks = []
        while True:
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                break
            data = os.read(fd, 65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks).decode("utf-8", errors="replace")
    except Exception:
        return ""


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if mode == "session-start":
            return session_start()  # stdin не читаем: он не нужен, а чтение незакрытого stdin вешает сессию
        if mode == "after-write":
            payload = read_stdin()
            try:
                payload = json.dumps(json.loads(payload), ensure_ascii=False)  # нормализуем экранирование путей
            except ValueError:
                pass
            return after_write(payload)
        if mode == "user-prompt":
            return user_prompt(read_stdin())
    except Exception as exc:  # хук не должен ронять сессию
        print(f"Стратег-хук: внутренняя ошибка {exc!r} — пропускаю", file=sys.stderr)
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # не ждать фоновый поток чтения stdin (Windows)
