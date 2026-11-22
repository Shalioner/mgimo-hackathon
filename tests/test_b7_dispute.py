"""Сценарий 7 (B7): спор человека с решающей фразой, источник и точечная поправка.

Покрывает:
- положительный/отрицательный контроль детекции возражения (хук UserPromptSubmit);
- цепочку пересмотра с сохранением истории: SRC -> CLM (new + supersede) -> HYP/DEC (needs_review, без выбора за человека);
- точечную правку через revise_lines.py (замена superseded ID на активный, запрет mark на superseded,
  защита от двойной пометки, коллизия 2-буквенных префиксов файлов, сводка PRM/EXP);
- соответствие skills/decision-support/SKILL.md и skills/self-audit/SKILL.md требованиям П4 и сценария 7.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import delegate as D  # noqa: E402
import ledger as L  # noqa: E402
import next as N  # noqa: E402
import revise_lines as R  # noqa: E402
import verify as V  # noqa: E402


HOOK = ROOT / "tools" / "hooks" / "strategist_hook.py"


def _run_hook(tmp_path: Path, prompt: str, sid: str = "b7-session") -> subprocess.CompletedProcess:
    env = dict(os.environ, STRATEGIST_HOOK_STATE=str(tmp_path / "hook_state"))
    payload = json.dumps({"prompt": prompt, "session_id": sid}, ensure_ascii=False)
    return subprocess.run(
        [sys.executable, str(HOOK), "user-prompt"],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=10,
    )


def _copy_example_with_outputs(tmp_path: Path) -> Path:
    proj = tmp_path / "example"
    shutil.copytree(ROOT / "projects" / "example", proj)
    (proj / "outputs").mkdir(exist_ok=True)
    (proj / "state/run_log.md").write_text(
        "| Время | Шаг | Действие | Уровень | Модель / инструмент | Результат | Стоимость |\n"
        "|---|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    (proj / "outputs/03_situation.md").write_text(
        "# Ситуация\n\n"
        "## Главное\n"
        "Удержание упало с 31% до 24%, основной причиной считалась синхронизация [CLM-001, CLM-004].\n\n"
        "## Подробности\n"
        "Синхронизация — 34% обращений в поддержку [CLM-002].\n"
        "Падение удержания в основном вызвано проблемами синхронизации [CLM-004].\n"
        "Синхронизация — главная причина ухода [HYP-001].\n"
        "Команда может переписать синхронизацию за один квартал [HYP-003].\n\n"
        "## Исторический снимок до CHG-001\n"
        "Прежняя формулировка опиралась на [CLM-004] и [HYP-001].\n",
        encoding="utf-8",
    )
    (proj / "outputs/04_options.md").write_text(
        "# Варианты\n\n"
        "## Главное\n"
        "Сравниваются четыре варианта; для ALT-001 ключевая опора — ускорение синхронизации [HYP-001].\n\n"
        "| Вариант | Суть | Основание |\n"
        "|---|---|---|\n"
        "| ALT-001 | Переписать синхронизацию | [HYP-001] |\n"
        "| ALT-002 | Семейные блокноты | [HYP-002] |\n",
        encoding="utf-8",
    )
    (proj / "outputs/05_product_strategy.md").write_text(
        "# Продуктовая стратегия\n\n"
        "## Главное\n"
        "Выбранный вектор ALT-001 требует проверки причины ухода [DEC-001, HYP-001].\n\n"
        "Обещание скорости синхронизации привязано к релизу [PRM-001].\n",
        encoding="utf-8",
    )
    (proj / "outputs/07_validation_plan.md").write_text(
        "# План проверки\n\n"
        "## Главное\n"
        "Проверяем прирост удержания D30 на 3 п.п. после обновления [EXP-001].\n",
        encoding="utf-8",
    )
    return proj


def _fake_delegate(reply: str, seen: dict):
    def run(argv):
        a = dict(zip(argv[::2], argv[1::2]))
        seen["task"] = Path(a["--prompt-file"]).read_text(encoding="utf-8")
        seen["kind"] = a["--task"]
        d = Path(a["--project"]) / "state" / "delegated"
        d.mkdir(parents=True, exist_ok=True)
        (d / "20990101-000000_revise-lines.md").write_text(reply, encoding="utf-8")
        return 0

    return run


# ---------- 1. Контроли сценария 7: спор с источником vs без источника / стиль ----------

def test_controls_dispute_hook_and_source_hold(tmp_path):
    # Положительный контроль: решающая фраза о выводе + ссылка на источник → триггер self-audit
    pos = _run_hook(
        tmp_path,
        "Нет, вывод о причине ухода неверный — по отчёту опроса (sources/survey_sync_2026-10.md) "
        "синхронизация затронула только 8%, пересмотрите основание CLM-004.",
        sid="pos-1",
    )
    assert pos.returncode == 0
    assert "skills/self-audit" in pos.stdout

    # Отрицательный контроль 1: решающая фраза БЕЗ нового документа при подтверждении источником
    # Хук сигналит self-audit, но правило анти-сикофантии запрещает менять CLM-002 без нового SRC
    neg_no_doc = _run_hook(
        tmp_path,
        "Это не так, я сам готовил сводку поддержки — там не 34%, а 12%, исправь немедленно.",
        sid="neg-1",
    )
    assert neg_no_doc.returncode == 0
    assert "skills/self-audit" in neg_no_doc.stdout
    proj = _copy_example_with_outputs(tmp_path)
    src_line = (proj / "sources/support_tickets_summary.md").read_text(encoding="utf-8").splitlines()[4]
    assert "34%" in src_line  # локатор подтверждает прежний факт; реплика человека не становится SRC
    claims = {c["id"]: c for c in yaml.safe_load((proj / "state/ledger/claims.yaml").read_text(encoding="utf-8"))}
    assert claims["CLM-002"].get("status", "active") == "active"
    assert "34%" in claims["CLM-002"]["statement"]

    # Отрицательный контроль 2: нейтральная правка стиля / объёма не запускает self-audit
    neg_style = _run_hook(
        tmp_path,
        "Сделай формулировку вывода в 05_product_strategy.md чуть короче и спокойнее по тону.",
        sid="neg-2",
    )
    assert neg_style.returncode == 0
    assert neg_style.stdout.strip() == ""


# ---------- 2. Red-тесты воспроизведённых дефектов в tools/revise_lines.py ----------

def test_revise_lines_supports_superseded_replacement_and_context(tmp_path, monkeypatch):
    """Спор → новый CLM-007 + supersede CLM-004 по CHG-001 → revise_lines должен передать CLM-007
    в задание и разрешить точечную замену [CLM-004] → [CLM-007], не ломая verify."""
    proj = _copy_example_with_outputs(tmp_path)

    claims_path = proj / "state/ledger/claims.yaml"
    claims = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    claims.append({
        "id": "CLM-007",
        "type": "interpretation",
        "statement": "Проблемы синхронизации касаются только 8% активных пользователей и не объясняют основную долю ухода.",
        "derived_from": ["CLM-001", "CLM-006"],
        "confidence": {"level": "medium", "rationale": "Уточнено по случайной выборке SRC-005"},
    })
    claims_path.write_text(yaml.safe_dump(claims, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert L.cmd_supersede(proj, "CLM-004", "CLM-007", "Пересмотрено по CHG-001: заменено на CLM-007.") == 0

    # Ограничим outputs одним файлом с одной устаревшей строкой, чтобы изолировать дефект supersede
    doc = proj / "outputs/03_situation.md"
    doc.write_text(
        "# Ситуация\n"
        "Падение удержания в основном вызвано проблемами синхронизации [CLM-004].\n"
        "Синхронизация — 34% обращений в поддержку [CLM-002].\n",
        encoding="utf-8",
    )
    (proj / "outputs/04_options.md").unlink()
    (proj / "outputs/05_product_strategy.md").unlink()
    (proj / "outputs/07_validation_plan.md").unlink()

    stale = [i for i in V.verify(proj, [], "CHG-001")["items"] if i["verdict"] == "stale?"]
    assert len(stale) == 1 and stale[0]["refs"] == ["CLM-004"]

    task, _keyed = R.build_task(L.Ledger(proj), "CHG-001", stale)
    # Дефект 1a: в задании должен быть виден статус superseded -> CLM-007 и сам текст CLM-007
    assert "superseded" in task and "CLM-007" in task
    assert "только 8% активных пользователей" in task

    seen = {}
    reply = json.dumps([
        {
            "key": "03:2",
            "action": "rewrite",
            "text": "Синхронизация затрагивает 8% пользователей и не объясняет уход [CLM-007].",
        }
    ], ensure_ascii=False)
    monkeypatch.setattr(D, "main", _fake_delegate(reply, seen))
    code = R.main([str(proj), "--change", "CHG-001", "--step", "8"])
    # Дефект 1b: раньше apply отбрасывал замену [CLM-004] -> [CLM-007] через ok_ids и возвращал 1
    assert code == 0
    lines = doc.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "Синхронизация затрагивает 8% пользователей и не объясняет уход [CLM-007]. (пересмотрено по CHG-001)"
    assert lines[2] == "Синхронизация — 34% обращений в поддержку [CLM-002]."  # нетронутая строка сохранена
    assert V.main([str(proj), "--change", "CHG-001"]) == 0


def test_revise_lines_rejects_mark_on_superseded_ref(tmp_path, monkeypatch):
    """Нельзя закрывать строку со ссылкой на superseded-объект действием mark — иначе останется stale-ref."""
    proj = _copy_example_with_outputs(tmp_path)
    claims_path = proj / "state/ledger/claims.yaml"
    claims = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    claims.append({
        "id": "CLM-007",
        "type": "interpretation",
        "statement": "Синхронизация не является главной причиной ухода.",
        "derived_from": ["CLM-006"],
        "confidence": {"level": "medium", "rationale": "SRC-005"},
    })
    claims_path.write_text(yaml.safe_dump(claims, allow_unicode=True, sort_keys=False), encoding="utf-8")
    L.cmd_supersede(proj, "CLM-004", "CLM-007", "Пересмотрено по CHG-001: заменено на CLM-007.")

    doc = proj / "outputs/03_situation.md"
    doc.write_text("Причина ухода — синхронизация [CLM-004].\n", encoding="utf-8")
    for name in ("04_options.md", "05_product_strategy.md", "07_validation_plan.md"):
        (proj / "outputs" / name).unlink()

    monkeypatch.setattr(D, "main", _fake_delegate(json.dumps([{"key": "03:1", "action": "mark"}]), {}))
    code = R.main([str(proj), "--change", "CHG-001"])
    assert code == 1
    assert "(проверено по CHG-001)" not in doc.read_text(encoding="utf-8")


def test_revise_lines_does_not_duplicate_revision_mark(tmp_path, monkeypatch):
    """Если модель уже дописала «(пересмотрено по CHG-001)», пометка не должна дублироваться."""
    proj = _copy_example_with_outputs(tmp_path)
    hyps_path = proj / "state/ledger/hypotheses.yaml"
    hyps = yaml.safe_load(hyps_path.read_text(encoding="utf-8"))
    for h in hyps:
        if h["id"] == "HYP-001":
            h["status_note"] = "Пересмотрено по CHG-001: ослаблена до 8%."
    hyps_path.write_text(yaml.safe_dump(hyps, allow_unicode=True, sort_keys=False), encoding="utf-8")

    doc = proj / "outputs/03_situation.md"
    doc.write_text("Синхронизация — главная причина ухода [HYP-001].\n", encoding="utf-8")
    for name in ("04_options.md", "05_product_strategy.md", "07_validation_plan.md"):
        (proj / "outputs" / name).unlink()

    reply = json.dumps([
        {
            "key": "03:1",
            "action": "rewrite",
            "text": "Синхронизация затрагивает 8% и не главная причина [HYP-001]. (пересмотрено по CHG-001)",
        }
    ], ensure_ascii=False)
    monkeypatch.setattr(D, "main", _fake_delegate(reply, {}))
    assert R.main([str(proj), "--change", "CHG-001"]) == 0
    txt = doc.read_text(encoding="utf-8").strip()
    assert txt.count("пересмотрено по CHG-001") == 1


def test_revise_lines_same_prefix_files_and_prm_exp_summary(tmp_path):
    """Два файла с одинаковыми первыми двумя символами не должны перетирать друг друга;
    object_summary должен показывать суть PRM (message) и EXP (expected_change)."""
    proj = _copy_example_with_outputs(tmp_path)
    for fname, oid in (("promises.yaml", "PRM-001"), ("experiments.yaml", "EXP-001")):
        p = proj / "state/ledger" / fname
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        for o in data:
            if o["id"] == oid:
                o["status_note"] = "Пересмотрено по CHG-001: уточнено под 8%."
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    (proj / "outputs/05_product_strategy.md").write_text("Обещание [PRM-001].\n", encoding="utf-8")
    (proj / "outputs/05_product_appendix.md").write_text("Проверка обещания [PRM-001] и опыта [EXP-001].\n", encoding="utf-8")
    for name in ("03_situation.md", "04_options.md", "07_validation_plan.md"):
        (proj / "outputs" / name).unlink()

    stale = [i for i in V.verify(proj, [], "CHG-001")["items"] if i["verdict"] == "stale?"]
    assert len(stale) == 2
    task, keyed = R.build_task(L.Ledger(proj), "CHG-001", stale)
    assert len(keyed) == 2
    assert "Заметки на всех устройствах" in task
    assert "Удержание D30 выше на 3 п.п." in task


# ---------- 3. Сквозной сценарий 7 на временной копии ----------

def test_scenario_7_end_to_end_dispute_source_trace_and_point_revision(tmp_path, monkeypatch):
    """Полный локальный сценарий 7:
    спор человека (решающая фраза) -> источник (add-source) -> утверждение (CLM-007 + supersede CLM-004)
    -> основание решения (trace/impact, HYP-004 + supersede HYP-001, DEC-001 -> needs_review с сохранением chosen)
    -> точечная поправка через revise_lines.py с сохранением истории и нетронутых веток."""
    proj = _copy_example_with_outputs(tmp_path)

    # 1. Решающая фраза спора с указанием на новый материал
    hook = _run_hook(
        tmp_path,
        "Нет, это неверно: по новой сводке телеметрии синхронизация ломается только у 8% пользователей, "
        "пересмотрите CLM-004 и решение DEC-001.",
        sid="e2e-7",
    )
    assert hook.returncode == 0 and "skills/self-audit" in hook.stdout

    # 2. Регистрируем новый источник (не изменяя исходный projects/example в репозитории)
    new_src = tmp_path / "telemetry_sync_2026_11.md"
    new_src.write_text(
        "# Телеметрия синхронизации, ноябрь 2026\n\n"
        "Доля активных пользователей с ошибками синхронизации: 8%.\n"
        "Основной отток в когорте 2026-10 связан с непониманием ценности тарифа.\n",
        encoding="utf-8",
    )
    assert L.cmd_add_source(
        proj, new_src, "Телеметрия синхронизации, ноябрь 2026",
        "document", "own", None, "high", None, "Проверка спора пользователя", today="2026-11-12",
    ) == 0

    # Проверяем trace основания решения ДО поправки
    ledger_before = L.Ledger(proj)
    assert "CLM-004" in {u for u, _ in ledger_before.upstream("HYP-001")}
    assert "HYP-001" in {u for u, _ in ledger_before.upstream("DEC-001")}

    # 3. Новое утверждение CLM-007 + supersede CLM-004, новая гипотеза HYP-004 + supersede HYP-001,
    #    событие CHG-002 (противоречие от SRC-006), DEC-001 -> needs_review БЕЗ выбора за человека
    claims_p = proj / "state/ledger/claims.yaml"
    claims = yaml.safe_load(claims_p.read_text(encoding="utf-8"))
    claims.append({
        "id": "CLM-007",
        "type": "interpretation",
        "statement": "Синхронизация затрагивает 8% пользователей и не является главной причиной падения удержания.",
        "evidence": [{"source": "SRC-006", "locator": "строки 3-4", "quote": "Доля активных пользователей с ошибками синхронизации: 8%."}],
        "derived_from": ["CLM-001", "CLM-006"],
        "confidence": {"level": "medium", "rationale": "Подтверждено опросом SRC-005 и телеметрией SRC-006"},
    })
    claims_p.write_text(yaml.safe_dump(claims, allow_unicode=True, sort_keys=False), encoding="utf-8")

    hyps_p = proj / "state/ledger/hypotheses.yaml"
    hyps = yaml.safe_load(hyps_p.read_text(encoding="utf-8"))
    hyps.append({
        "id": "HYP-004",
        "kind": "hypothesis",
        "statement": "Ускорение синхронизации даст прирост удержания D30 не более 1 п.п., так как затронуто 8% пользователей.",
        "based_on": ["CLM-007"],
        "confidence": {"level": "medium", "rationale": "Пересчитано на базе CLM-007 и SRC-006"},
        "would_change_decision": True,
    })
    hyps_p.write_text(yaml.safe_dump(hyps, allow_unicode=True, sort_keys=False), encoding="utf-8")

    changes_p = proj / "state/ledger/changes.yaml"
    changes = yaml.safe_load(changes_p.read_text(encoding="utf-8"))
    changes.append({
        "id": "CHG-002",
        "date": "2026-11-12",
        "source": "SRC-006",
        "summary": "Телеметрия подтвердила 8% пользователей с ошибками синхронизации.",
        "effects": [{"target": "CLM-004", "effect": "contradicts", "note": "Синхронизация не основная причина."}],
        "processed": False,
    })
    changes_p.write_text(yaml.safe_dump(changes, allow_unicode=True, sort_keys=False), encoding="utf-8")

    assert L.cmd_supersede(proj, "CLM-004", "CLM-007", "Пересмотрено по CHG-002: заменено на CLM-007.") == 0
    assert L.cmd_supersede(proj, "HYP-001", "HYP-004", "Пересмотрено по CHG-002: заменено на HYP-004.") == 0

    # ALT-001 переводим на HYP-004 (без переписывания ALT-002..004)
    alts_p = proj / "state/ledger/alternatives.yaml"
    alts = yaml.safe_load(alts_p.read_text(encoding="utf-8"))
    for a in alts:
        if a["id"] == "ALT-001":
            a["supported_by"] = ["HYP-004", "HYP-003"]
            a["scores"]["влияние на удержание"] = {"value": 2, "rationale": "Ограничено 8% аудитории", "refs": ["HYP-004"]}
            a["status_note"] = "Пересмотрено по CHG-002: влияние снижено с 4 до 2."
    alts_p.write_text(yaml.safe_dump(alts, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # DEC-001: только state: needs_review + rationale, chosen остаётся прежним до ответа человека (C2)
    decs_p = proj / "state/ledger/decisions.yaml"
    decs = yaml.safe_load(decs_p.read_text(encoding="utf-8"))
    decs[0]["state"] = "needs_review"
    decs[0]["based_on"] = ["CLM-007", "HYP-004"]
    decs[0]["rationale"] = "Пересмотрено по CHG-002: CLM-004/HYP-001 заменены на CLM-007/HYP-004; требуется новый выбор человека на C2."
    decs_p.write_text(yaml.safe_dump(decs, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # Проверяем validate и next.py — DEC-001 ждёт человека, CHG-002 открыт, ошибок 0
    val = L.validate(L.Ledger(proj))
    assert val.errors == []
    q_ids = {item["id"]: item for item in N.build_queue(proj, N.iso_date("2026-11-12"))["items"]}
    assert "DEC-001" in q_ids and "CHG-002" in q_ids
    assert yaml.safe_load(decs_p.read_text(encoding="utf-8"))[0]["chosen"] == "ALT-001"

    # 4. Точечная правка устаревших строк через revise_lines.py
    stale = [i for i in V.verify(proj, [], "CHG-002")["items"] if i["verdict"] == "stale?"]
    assert len(stale) == 6  # 03:4,7,8,9 + 04:4,8 + 05:4 (без исторического снимка в 03 и без ALT-002)
    task, keyed = R.build_task(L.Ledger(proj), "CHG-002", stale)
    assert "Исторический снимок" not in task
    assert "ALT-002" not in task

    replies = []
    for k, it in keyed.items():
        if it["file"] == "03_situation.md" and it["line"] == 4:
            replies.append({"key": k, "action": "rewrite", "text": "Удержание упало с 31% до 24%, роль синхронизации снижена после проверки [CLM-001, CLM-007]."})
        elif it["file"] == "03_situation.md" and it["line"] == 8:
            replies.append({"key": k, "action": "rewrite", "text": "Синхронизация не объясняет основную долю падения удержания [CLM-007]."})
        elif it["file"] == "03_situation.md" and it["line"] == 9:
            replies.append({"key": k, "action": "rewrite", "text": "Ускорение синхронизации даст не более 1 п.п. прироста D30 [HYP-004]."})
        elif it["file"] == "04_options.md" and it["line"] == 4:
            replies.append({"key": k, "action": "rewrite", "text": "Сравниваются четыре варианта; для ALT-001 ожидается прирост не более 1 п.п. [HYP-004]."})
        elif it["file"] == "04_options.md" and it["line"] == 8:
            replies.append({"key": k, "action": "rewrite", "text": "| ALT-001 | Переписать синхронизацию | [HYP-004] |"})
        elif it["file"] == "05_product_strategy.md" and it["line"] == 4:
            replies.append({"key": k, "action": "rewrite", "text": "Прежний вектор ALT-001 отправлен на пересмотр до ответа человека [DEC-001, HYP-004]."})

    monkeypatch.setattr(D, "main", _fake_delegate(json.dumps(replies, ensure_ascii=False), {}))
    assert R.main([str(proj), "--change", "CHG-002", "--step", "8"]) == 0

    # Проверяем: нетронутые строки, исторический снимок и ALT-002 не изменились; stale? = 0, но историческая секция
    # в 03 содержит прежние ссылки [CLM-004] — проверяем что активные строки 03/04/05 чисты от stale?
    after_res = V.verify(proj, [], "CHG-002")
    assert "stale?" not in after_res["counts"]
    s03 = (proj / "outputs/03_situation.md").read_text(encoding="utf-8")
    assert "Синхронизация — 34% обращений в поддержку [CLM-002]." in s03
    assert "Команда может переписать синхронизацию за один квартал [HYP-003]." in s03
    assert "## Исторический снимок до CHG-001\nПрежняя формулировка опиралась на [CLM-004] и [HYP-001]." in s03
    s04 = (proj / "outputs/04_options.md").read_text(encoding="utf-8")
    assert "| ALT-002 | Семейные блокноты | [HYP-002] |" in s04


# ---------- 4. Контракт навыков (П4 контекст пересмотра и сценарий 7) ----------

def test_decision_support_skill_has_p4_resume_without_missing_tools():
    text = (ROOT / "skills/decision-support/SKILL.md").read_text(encoding="utf-8")
    assert "## Возврат после паузы" in text
    for req in ("state/progress.md", "state/run_log.md", "next.py", "awaiting_human", "ledger.py brief", "trace", "impact"):
        assert req in text, f"В skills/decision-support/SKILL.md не хватает {req}"
    # Compact main сохраняет версионное согласование C3 перед предъявлением пакета.
    assert (ROOT / "tools/c3_approval.py").is_file()
    assert "tools/c3_approval.py prepare projects/<case>" in text
    assert "log_or_defer" not in text


def test_self_audit_skill_covers_p4_and_point_revision():
    text = (ROOT / "skills/self-audit/SKILL.md").read_text(encoding="utf-8")
    # Анти-сикофантия не должна быть ослаблена
    assert "Но слова человека не отменяют документ" in text
    # Адресный контекст П4 и точечная поправка с сохранением истории
    assert "progress.md" in text
    assert "revise_lines.py" in text
    assert "supersede" in text
