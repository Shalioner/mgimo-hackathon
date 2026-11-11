"""Тесты хуков (tools/hooks/strategist_hook.py) и их конфигурации для Claude Code и Codex."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "tools" / "hooks" / "strategist_hook.py"


def run(mode, payload=None, timeout=30):
    return subprocess.run([sys.executable, str(HOOK), mode], input=payload, capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout, cwd=str(ROOT))


def test_session_start_prints_status():
    r = run("session-start")
    assert r.returncode == 0 and "example" in r.stdout


def test_after_write_ignores_non_ledger_files():
    assert run("after-write", json.dumps({"tool_input": {"file_path": "README.md"}})).returncode == 0


def test_after_write_passes_valid_ledger():
    p = {"tool_name": "Edit", "tool_input": {"file_path": str(ROOT / "projects/example/state/ledger/claims.yaml")}}
    assert run("after-write", json.dumps(p)).returncode == 0


def test_after_write_blocks_broken_ledger_from_codex_patch():
    tmp = ROOT / "projects" / "zz_hooktest"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(ROOT / "projects" / "example", tmp)
    try:
        f = tmp / "state/ledger/claims.yaml"
        f.write_text(f.read_text(encoding="utf-8").replace("type: fact", "type: факт", 1), encoding="utf-8")
        p = {"tool_name": "apply_patch", "tool_input": {"command": "*** Update File: projects/zz_hooktest/state/ledger/claims.yaml"}}
        r = run("after-write", json.dumps(p))
        assert r.returncode == 2 and "CLM-001" in r.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_after_write_does_not_hang_on_open_stdin():
    proc = subprocess.Popen([sys.executable, str(HOOK), "after-write"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ROOT))
    try:
        assert proc.wait(timeout=10) == 0  # stdin так и не закрыт
    finally:
        proc.kill()


def test_hook_configs_point_to_same_script():
    claude = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    codex = json.loads((ROOT / ".codex/hooks.json").read_text(encoding="utf-8"))
    for cfg in (claude, codex):
        cmds = [h["command"] for ev in ("SessionStart", "UserPromptSubmit", "PostToolUse") for blk in cfg["hooks"][ev] for h in blk["hooks"]]
        assert all("strategist_hook.py" in c for c in cmds) and len(cmds) == 3


OBJECTIONS = ["это не так, перечитай чат", "как же ты меня бесишь", "ты меня не понял", "опять не то",
              "я же говорил про 299", "что за хуйня", "That's wrong", "Ты вообще не понимаешь задачу"]
NOT_OBJECTIONS = ["это не так уж плохо, продолжай", "продолжай", "сделай короче", "заебись, работает!",
                  "почему здесь 31%?", "проверь «это не так» в цитате", "> это неверно\nпосмотри", "корабля, рубля"]


def test_user_prompt_hints_self_audit_on_objection():
    for text in OBJECTIONS:
        r = run("user-prompt", json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": text}, ensure_ascii=False))
        assert r.returncode == 0 and "skills/self-audit" in r.stdout, text


def test_user_prompt_silent_on_ordinary_messages():
    for text in NOT_OBJECTIONS:
        r = run("user-prompt", json.dumps({"prompt": text}, ensure_ascii=False))
        assert r.returncode == 0 and r.stdout.strip() == "", text


def test_user_prompt_never_fails_on_garbage():
    assert run("user-prompt", "не json").returncode == 0
    assert run("user-prompt", "").returncode == 0


# --- словарь возражений (tools/hooks/objection_lexicon.py): языки, маскировка, двузначные слова ---
sys.path.insert(0, str(ROOT / "tools" / "hooks"))
import objection_lexicon as LEX  # noqa: E402

SWEARS = [
    "нахуя ты это сделал", "охуел совсем", "заебал уже", "распиздяй", "долбоёб", "съебись", "проебут",
    "сука, опять", "бляяяя",                          # повтор букв
    "х*й знает что", "бл*ть", "f**k this",            # звёздочки
    "х.у.й", "xyйня какая-то", "пи3дец", "6ля",       # точки, латиница, цифры
    "fuсk",                                           # кириллическая «с» в латинском слове
    "nahuy", "pizdec", "blyat",                       # транслит
    "курва", "kurwa", "chuj", "this is bullshit", "wtf", "scheiße", "putain", "merde",
    "joder", "mierda", "porra", "caralho", "cazzo", "vaffanculo", "siktir", "amk",
    "你他妈的", "傻逼", "卧槽", "くそ", "씨발",
]
PHRASES = [
    "это не так", "ты меня не понял", "це не так", "to nie tak", "not what I asked", "das ist falsch",
    "c'est faux", "está mal", "isso está errado", "non hai capito", "yanlış", "不对", "わかってない", "틀렸어",
]
LOOKALIKES = [
    "сукно на столе", "хуже не стало", "небо и себе", "ебитда выросла на 6%", "putin", "computadora",
    "shift+tab", "**Главное** в отчёте", "2*3=6 и N*price", "360x640 и 45s", "Scheibe", "употреблять",
    "оскорблять", "хлеба", "требую отчёт", "скребок", "хулиган", "хутор", "страхуй", "сравнение",
    "assistant", "passport", "cocktail", "Dickens", "скипидар", "дебетовая карта", "подъезд", "учебник",
    "гребля", "shiitake", "Sussex", "z.a.e.b.a.l — название навыка", "z a e b a l",
]


def test_lexicon_catches_swearing_in_many_languages_and_masks():
    for text in SWEARS:
        assert "мат" in LEX.detect(text), text


def test_lexicon_catches_objection_phrases_in_many_languages():
    for text in PHRASES:
        assert LEX.detect(text), text


def test_lexicon_ignores_lookalike_words():
    for text in LOOKALIKES:
        assert LEX.detect(text) == [], text


def test_lexicon_praise_is_not_objection_unless_complaint():
    assert LEX.detect("заебись, работает!") == [] and LEX.detect("fucking awesome") == []
    assert "мат" in LEX.detect("охуенно, но опять не то")


# Деловые возражения: так пишут в компании, где переписку с ИИ могут читать коллеги.
BUSINESS_OBJECTIONS = [
    "Здесь пониженное качество, хотя должно быть выше",
    "Сильно завышены показатели, требуется перерасчёт",
    "Не очень реалистично, надо провести дополнительные исследования по цифрам",
    "Ты не так сделал, перечитай сессию",
    "Цифры по ARPU выглядят завышенными",
    "Прогноз выручки слишком оптимистичный",
    "Откуда эта цифра 46%?",
    "Это противоречит данным из брифа",
    "Не учтены расходы на поддержку",
    "Раздел про конкурентов проработан поверхностно",
    "Расчёт некорректный, цифры не сходятся",
    "Мне кажется, здесь занижен CAC",
    "Складывается впечатление, что данные взяты с потолка",
    "Выборка маленькая, нерепрезентативно",
    "Мы это уже обсуждали на прошлой неделе",
    "Сомневаюсь, что абоненты заплатят 299 ₽",
    "The numbers don't add up",
    "Where does this number come from?",
]
BUSINESS_NEUTRAL = [
    "Посчитай ARPU и EBITDA по РТС",
    "Сравни ЕБИТДА и ебида у РТТ и РТС",
    "Посчитай OIBDA, ОИБДА, ЕБИТ и EBIT",
    "ЕБС и ЕБРР в источниках",
    "ПИД-регулятор и ВОЛС",
    "CAPEX, OPEX, LTV, CAC, NPS, CSI, MAU, DAU",
    "ФАС, РКН, СОРМ, ЦОД, МСП, B2B, B2C",
    "Какие показатели у МегаФона за 2025?",
    "Проведи исследование рынка eSIM",
    "Добавь раздел про качество связи",
    "Качество сети 5G у нас выше рынка",
    "Подготовь план эксперимента со stop/go",
    "Выручка выросла на 12%",
    "Почему ты выбрал вариант A?",
]


def test_lexicon_catches_polite_business_objections():
    for text in BUSINESS_OBJECTIONS:
        assert LEX.detect(text), text


def test_lexicon_ignores_business_terms_and_neutral_tasks():
    for text in BUSINESS_NEUTRAL:
        assert LEX.detect(text) == [], text
