"""Тесты линта доказательности (tools/evidence.py, M12)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import evidence as EV  # noqa: E402
import ledger as L  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "evidence"
REQUIRED_KEYS = {"code", "status", "message", "file", "line", "ids"}
ALLOWED_STATUSES = {"ok", "warn", "error", "not_assessed"}


def test_matching_number_reports_that_context_needs_separate_review():
    quote = "За июнь 2026 доля повторных покупок среди покупателей сегмента travel составила 50%."
    for before, after in [
        ("июнь", "сентябрь"),
        ("среди покупателей", "среди всех посетителей"),
        ("travel", "business"),
    ]:
        result = EV.assess_quote_numbers({
            "statement": quote.replace(before, after),
            "evidence": [{"quote": quote}],
        })
        assert result["reason"] == "numbers_matched"
        assert all(word in result["detail"].lower() for word in ("сегмент", "период", "знаменатель"))


def _make_proj(tmp_path: Path, sources=None, claims=None, hypotheses=None, alternatives=None, decisions=None) -> Path:
    proj = tmp_path / "case"
    (proj / "state" / "ledger").mkdir(parents=True, exist_ok=True)
    (proj / "sources").mkdir(parents=True, exist_ok=True)
    mapping = {
        "sources.yaml": sources,
        "claims.yaml": claims,
        "hypotheses.yaml": hypotheses,
        "alternatives.yaml": alternatives,
        "decisions.yaml": decisions,
    }
    for fname, data in mapping.items():
        if data is not None:
            (proj / "state" / "ledger" / fname).write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
    return proj


def test_narrow_fact_locale_scale_and_single_primary_source_ok(capsys):
    proj = FIXTURES / "narrow_fact"
    items = EV.check(proj)
    assert len(items) == 1
    it = items[0]
    assert set(it.keys()) == REQUIRED_KEYS
    assert it["status"] == "ok"  # info только: один первичный источник узкого факта
    assert it["code"] == "evidence_single_source"
    assert it["ids"] == ["CLM-001"]
    assert it["file"] == "state/ledger/claims.yaml"
    assert it["line"] == 1
    assert EV.main([str(proj), "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == items


def test_forecast_as_fact_warns_and_forecast_type_does_not(tmp_path):
    proj = _make_proj(
        tmp_path,
        sources=[{"id": "SRC-001", "title": "Отчёт", "kind": "website", "access": "open"}],
        claims=[
            {
                "id": "CLM-001",
                "type": "fact",
                "statement": "Выручка достигнет $500 млн к концу года (прогноз).",
                "evidence": [{"source": "SRC-001", "quote": "Revenue will reach $500 million."}],
            },
            {
                "id": "CLM-002",
                "type": "forecast",
                "statement": "Ожидается рост на 20%.",
                "evidence": [{"source": "SRC-001", "quote": "Expected growth 20%."}],
            },
        ],
    )
    items = EV.check(proj)
    warns = [i for i in items if i["code"] == "evidence_forecast_as_fact"]
    assert len(warns) == 1
    assert warns[0]["status"] == "warn"
    assert warns[0]["ids"] == ["CLM-001"]
    assert EV.main([str(proj)]) == 0


def test_number_not_in_quote_is_error_with_ranges_and_dates(tmp_path):
    proj = _make_proj(
        tmp_path,
        sources=[{"id": "SRC-001", "title": "Цены", "kind": "website", "access": "open"}],
        claims=[
            {
                "id": "CLM-001",
                "type": "fact",
                "statement": "Цены на 01.09.2026: в 5–15 раз дороже; Европа 7–15 дней $9–18.",
                "as_of": "2026-09-01",
                "evidence": [{"source": "SRC-001", "quote": "В 5-15 раз дороже конкурентов."}],
            },
            {
                "id": "CLM-002",
                "type": "fact",
                "statement": "У нас два среза данных и диапазон от 10 до 20%.",
                "evidence": [{"source": "SRC-001", "quote": "Показатель 10–20%."}],
            },
        ],
    )
    items = EV.check(proj)
    errs = [i for i in items if i["code"] == "evidence_number_not_in_quote"]
    assert len(errs) == 1
    assert errs[0]["status"] == "error"
    assert errs[0]["ids"] == ["CLM-001"]
    # 01.09.2026 покрыто as_of, 5 и 15 есть в 5-15, отсутствуют 7, 9, 18
    assert "(7, 9, 18)" in errs[0]["message"]
    assert "2026" not in errs[0]["message"]
    assert EV.main([str(proj)]) == 1


def test_filtered_trace_excludes_contradicted_by_and_objections_and_unchosen_options(tmp_path):
    proj = _make_proj(
        tmp_path,
        sources=[
            {"id": "SRC-001", "title": "Официальная сводка", "kind": "dataset", "access": "own"},
            {"id": "SRC-002", "title": "Синтетический опрос", "kind": "synthetic_interview", "access": "own"},
            {"id": "SRC-003", "title": "Сторонний блог", "kind": "vendor_blog", "access": "open"},
        ],
        claims=[
            {
                "id": "CLM-001",
                "type": "fact",
                "statement": "Конверсия 12%.",
                "evidence": [{"source": "SRC-001", "quote": "12%"}],
                "contradicted_by": ["CLM-002"],
            },
            {
                "id": "CLM-002",
                "type": "interpretation",
                "statement": "Синтетический респондент недоволен ценой.",
                "evidence": [{"source": "SRC-002", "quote": "дорого"}],
            },
            {
                "id": "CLM-003",
                "type": "fact",
                "statement": "Цена конкурента 300 ₽.",
                "evidence": [{"source": "SRC-003", "quote": "300 ₽"}],
            },
        ],
        alternatives=[
            {
                "id": "ALT-001",
                "title": "Выбранный вариант",
                "kind": "invest",
                "supported_by": ["CLM-001"],
                "objections": [{"text": "Риск", "refs": ["CLM-002", "CLM-003"]}],
            },
            {
                "id": "ALT-002",
                "title": "Отвергнутый вариант",
                "kind": "status_quo",
                "supported_by": ["CLM-003"],
            },
        ],
        decisions=[
            {
                "id": "DEC-001",
                "question": "Что выбрать?",
                "options": ["ALT-001", "ALT-002"],
                "state": "decided",
                "chosen": "ALT-001",
                "based_on": ["CLM-001"],
                "decided_by": "руководитель продукта",
                "decided_at": "2026-11-10",
                "rationale": "Опора на CLM-001.",
                "review_triggers": ["падение ниже 10%"],
            }
        ],
    )
    ledger = L.Ledger(proj)
    tr_dec = EV.filtered_trace(ledger, "DEC-001")
    # Ни SRC-002 (из contradicted_by и objections), ни SRC-003 (из objections и unchosen ALT-002) не должны попасть в trace!
    assert tr_dec["sources"] == ["SRC-001"]
    assert tr_dec["flags"]["synthetic_only"] is False
    assert tr_dec["flags"]["contradicted"] is True
    assert tr_dec["flags"]["single_source"] is True

    tr_clm = EV.filtered_trace(ledger, "CLM-001")
    assert tr_clm["sources"] == ["SRC-001"]
    assert "CLM-002" not in tr_clm["nodes"]


def test_synthetic_hypothesis_and_stale_flags_and_what_to_gather_no_threshold(tmp_path, capsys):
    proj = _make_proj(
        tmp_path,
        sources=[{"id": "SRC-001", "title": "Синтетика", "kind": "synthetic_interview", "access": "own"}],
        claims=[
            {
                "id": "CLM-001",
                "type": "interpretation",
                "statement": "Синтетический ответ без фактов.",
                "evidence": [{"source": "SRC-001", "quote": "мне кажется так"}],
                "status": "superseded",
                "superseded_by": "CLM-002",
            },
            {
                "id": "CLM-002",
                "type": "interpretation",
                "statement": "Новая интерпретация на синтетике.",
                "evidence": [{"source": "SRC-001", "quote": "новый ответ"}],
            },
        ],
        hypotheses=[
            {"id": "HYP-001", "statement": "Гипотеза на устаревшей базе.", "based_on": ["CLM-001"]},
        ],
        alternatives=[
            {"id": "ALT-001", "title": "Ход", "kind": "change", "supported_by": ["HYP-001", "CLM-002"]},
            {"id": "ALT-002", "title": "Статус-кво", "kind": "status_quo"},
        ],
        decisions=[
            {
                "id": "DEC-001",
                "question": "Что делаем?",
                "options": ["ALT-001", "ALT-002"],
                "state": "decided",
                "chosen": "ALT-001",
                "based_on": ["HYP-001"],
                "decided_by": "руководитель",
                "decided_at": "2026-11-10",
                "rationale": "Тест.",
                "review_triggers": ["нет спроса"],
            }
        ],
    )
    items = EV.check(proj)
    assert all(set(i.keys()) == REQUIRED_KEYS and i["status"] in ALLOWED_STATUSES for i in items)
    codes = {i["code"] for i in items}
    assert {"evidence_synthetic_only", "evidence_hypothesis_only", "evidence_stale", "evidence_single_source"} <= codes

    rc = EV.main([str(proj)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "## Что добрать" in out
    # В «Что добрать» не должно быть числовых порогов вроде «нужно N интервью/источников»
    gather_part = out.split("## Что добрать", 1)[1]
    assert not re.search(r"\b\d+\s*(?:интервью|источник|респондент)", gather_part, re.I)


def test_missing_quote_and_locator_bypass_blocked():
    no_quote = EV.missing_numbers_in_quote(
        {
            "type": "fact",
            "statement": "Конверсия 42% на 500 установках",
            "evidence": [{"source": "SRC-001", "locator": "Таблица 1"}],
        }
    )
    assert no_quote == ["42", "500"]

    in_loc = EV.missing_numbers_in_quote(
        {
            "type": "fact",
            "statement": "Установок 3 100, активных 100",
            "evidence": [{"source": "SRC-001", "quote": "активных 100", "locator": "строка установок 3 100"}],
        }
    )
    assert in_loc == ["3 100"]


def test_pdf_link_and_mixed_evidence_require_manual_check():
    objects = {
        "SRC-001": {"kind": "document", "url": "https://example.invalid/report.PDF?version=2#page=4"},
        "SRC-002": {"kind": "web", "url": "https://example.invalid/report.html"},
    }
    pdf = {"source": "SRC-001", "locator": "стр. 4"}
    text = {"source": "SRC-002", "quote": "В выборке 500 установок."}
    claim = {"type": "fact", "statement": "Доля 17%.", "evidence": [pdf]}

    assert EV.assess_quote_numbers(claim, objects)["status"] == "not_assessed"
    claim.update(statement="Доля 17% на 500 установках.", evidence=[pdf, text])
    result = EV.assess_quote_numbers(claim, objects)
    assert result["status"] == "not_assessed"
    assert "17" in result["detail"]
    assert "500" not in result["detail"]
    claim["evidence"] = [text, {"source": "SRC-002", "locator": "таблица на сайте"}]
    assert EV.assess_quote_numbers(claim, objects)["status"] == "error"


def test_thousands_verbal_scale_and_retention_windows():
    assert EV.missing_numbers_in_quote(
        {
            "type": "fact",
            "statement": "Тарифы 1 000–2 000 ₽.",
            "evidence": [{"source": "SRC-001", "quote": "Тарифы от 1 000 до 2 000 рублей."}],
        }
    ) == []
    assert EV.missing_numbers_in_quote(
        {
            "type": "fact",
            "statement": "Опрошено 1 200 пользователей.",
            "evidence": [{"source": "SRC-001", "quote": "We surveyed 1,200 users."}],
        }
    ) == []
    assert EV.missing_numbers_in_quote(
        {
            "type": "fact",
            "statement": "Объём от 5 до 15 млн ₽.",
            "evidence": [{"source": "SRC-001", "quote": "Объём 5–15 млн ₽."}],
        }
    ) == []
    assert EV.missing_numbers_in_quote(
        {
            "type": "fact",
            "statement": "Удержание на 30-й день — 31%.",
            "evidence": [{"source": "SRC-001", "quote": "D30 retention was 31%."}],
        }
    ) == []
    assert EV.missing_numbers_in_quote(
        {
            "type": "fact",
            "statement": "Удержание D30 — 31%.",
            "evidence": [{"source": "SRC-001", "quote": "Удержание D7 — 31%."}],
        }
    ) == ["30"]


def test_filtered_stale_ignores_objections_and_single_source_not_ok_on_errors(tmp_path):
    proj = _make_proj(
        tmp_path,
        sources=[
            {"id": "SRC-001", "title": "Выгрузка", "kind": "dataset", "access": "own", "reliability": "high"},
            {"id": "SRC-002", "title": "Отчёт", "kind": "dataset", "access": "own", "reliability": "high"},
            {"id": "SRC-003", "title": "Блог вендора", "kind": "website", "access": "open", "reliability": "low"},
        ],
        claims=[
            {
                "id": "CLM-001",
                "type": "fact",
                "statement": "Конверсия 10%.",
                "evidence": [{"source": "SRC-001", "quote": "10%"}],
                "confidence": {"level": "high", "rationale": "ok"},
            },
            {
                "id": "CLM-002",
                "type": "fact",
                "statement": "Доля 20%.",
                "evidence": [{"source": "SRC-002", "quote": "20%"}],
                "confidence": {"level": "high", "rationale": "ok"},
            },
            {
                "id": "CLM-003",
                "type": "fact",
                "statement": "Старый факт 5%.",
                "evidence": [{"source": "SRC-002", "quote": "5%"}],
                "status": "superseded",
                "superseded_by": "CLM-002",
            },
            {
                "id": "CLM-004",
                "type": "fact",
                "statement": "Блоговая оценка вырастет на 99%.",
                "evidence": [{"source": "SRC-003", "quote": "Без цифр"}],
                "confidence": {"level": "low", "rationale": "blog"},
            },
        ],
        alternatives=[
            {
                "id": "ALT-001",
                "title": "Основной",
                "kind": "invest",
                "supported_by": ["CLM-001", "CLM-002"],
                "objections": [{"text": "Возражение на устаревшем", "refs": ["CLM-003"]}],
            },
            {
                "id": "ALT-002",
                "title": "Отвергнутый",
                "kind": "status_quo",
                "supported_by": ["CLM-003"],
            },
        ],
        decisions=[
            {
                "id": "DEC-001",
                "question": "Что выбрать?",
                "options": ["ALT-001", "ALT-002"],
                "state": "decided",
                "chosen": "ALT-001",
                "based_on": ["CLM-001", "CLM-002", "CLM-004"],
                "decided_by": "руководитель",
                "decided_at": "2026-11-10",
                "rationale": "ok",
                "review_triggers": ["t"],
            }
        ],
    )
    items = EV.check(proj)
    dec_codes = [f["code"] for f in items if f["ids"] == ["DEC-001"]]
    assert "evidence_stale" not in dec_codes

    clm4_single = [f for f in items if f["ids"] == ["CLM-004"] and f["code"] == "evidence_single_source"]
    assert len(clm4_single) == 1
    assert clm4_single[0]["status"] == "warn"


def test_nontext_pdf_csv_locator_and_derived_forecast_f3(tmp_path):
    proj = _make_proj(
        tmp_path,
        sources=[
            {"id": "SRC-001", "title": "UI", "kind": "image", "access": "own", "path": "sources/ui.png", "reliability": "high"},
            {"id": "SRC-002", "title": "PDF", "kind": "document", "access": "open", "path": "sources/r.pdf", "reliability": "high"},
            {"id": "SRC-003", "title": "Base", "kind": "document", "access": "own", "path": "sources/b.md", "reliability": "high"},
            {"id": "SRC-004", "title": "Metrics", "kind": "table", "access": "own", "path": "sources/m.csv", "reliability": "high"},
        ],
        claims=[
            {
                "id": "CLM-001",
                "type": "fact",
                "statement": "Цена на экране — 299 ₽.",
                "evidence": [{"source": "SRC-001", "locator": "экран 2"}],
                "confidence": {"level": "high", "rationale": "img"},
            },
            {
                "id": "CLM-002",
                "type": "fact",
                "statement": "Доля сегмента — 35%.",
                "evidence": [{"source": "SRC-002", "locator": "стр. 4"}],
                "confidence": {"level": "high", "rationale": "pdf"},
            },
            {
                "id": "CLM-003",
                "type": "fact",
                "statement": "Тариф 299 ₽, конверсия 10%.",
                "evidence": [{"source": "SRC-003", "quote": "Тариф 299 ₽, конверсия 10%."}],
                "confidence": {"level": "high", "rationale": "txt"},
            },
            {
                "id": "CLM-004",
                "type": "forecast",
                "statement": "Ожидаемый ARPU — 29,9 ₽.",
                "derived_from": ["CLM-003"],
                "evidence": [{"source": "SRC-003", "quote": "Тариф 299 ₽, конверсия 10%."}],
                "confidence": {"level": "medium", "rationale": "calc"},
            },
            {
                "id": "CLM-005",
                "type": "fact",
                "statement": "D30 удержание 31%.",
                "evidence": [{"source": "SRC-004", "locator": "row 2, col d30"}],
                "confidence": {"level": "high", "rationale": "csv"},
            },
            {
                "id": "CLM-006",
                "type": "fact",
                "statement": "Прямой факт: 45% пользователей.",
                "evidence": [{"source": "SRC-003", "locator": "строка 1"}],
                "confidence": {"level": "high", "rationale": "txt"},
            },
        ],
    )
    (proj / "sources" / "ui.png").write_bytes(b"\x89PNG\r\n")
    (proj / "sources" / "r.pdf").write_bytes(b"%PDF-1.4\n")
    (proj / "sources" / "b.md").write_text("Тариф 299 ₽, конверсия 10%.\n", encoding="utf-8")
    (proj / "sources" / "m.csv").write_text("d30\n31\n", encoding="utf-8")

    items = EV.check(proj)
    by_id_code = {(cid, f["code"]): f for f in items for cid in f["ids"]}

    # image, pdf, csv locator без quote -> not_assessed, а не error
    for cid in ("CLM-001", "CLM-002", "CLM-005"):
        assert by_id_code[(cid, "evidence_quote_not_assessed")]["status"] == "not_assessed"
        assert (cid, "evidence_number_not_in_quote") not in by_id_code
        assert by_id_code[(cid, "evidence_single_source")]["status"] == "warn"

    # Расчётный forecast с валидным derived_from -> без error
    assert ("CLM-004", "evidence_number_not_in_quote") not in by_id_code
    # Прямой текстовый факт с числом без quote -> по-прежнему error
    assert by_id_code[("CLM-006", "evidence_number_not_in_quote")]["status"] == "error"

    # Эталонный projects/example теперь не падает с error из-за CSV-locator
    assert EV.main([str(ROOT / "projects" / "example")]) == 0


def test_q1_locale_comma_range_and_stale_contradicted_single_source_f4(tmp_path):
    # 1. Граничные и позитивные кейсы F4: Q1 2026, 0,250 USD, $1,200 с кириллицей, от $5 до $15 млн
    pairs = [
        ("В Q1 2026 выручка выросла на 15%.", "In 2026 (Q1), revenue grew by 15%."),
        ("ARPU 0,250 USD.", "ARPU 0.25 USD."),
        ("Тариф роуминга — $1,200 в год.", "Тариф роуминга — 1 200 долларов в год."),
        ("Объём рынка от $5 до $15 млн.", "Объём рынка $5–15 млн."),
        ("Окно D7 — 500 активаций.", "500 activations on D7."),
    ]
    for s, q in pairs:
        assert EV.missing_numbers_in_quote(
            {"type": "fact", "statement": s, "evidence": [{"source": "SRC-001", "quote": q}]}
        ) == [], f"failed pair: {s} vs {q}"

    # Негативные соседние значения не сливаются
    assert EV.missing_numbers_in_quote(
        {"type": "fact", "statement": "В Q1 2025 выручка 14%.", "evidence": [{"source": "SRC-001", "quote": "In Q1 2026 revenue was 15%."}]}
    ) == ["2025", "14"]

    # 2. Мутационная проверка 1000x и неоднозначная смешанная локаль
    proj_amb = _make_proj(
        tmp_path / "amb",
        sources=[{"id": "SRC-001", "title": "SensorTower", "kind": "dataset", "access": "open", "reliability": "high"}],
        claims=[
            {
                "id": "CLM-001",
                "type": "fact",
                "statement": "Загрузок 1 200.",
                "evidence": [{"source": "SRC-001", "quote": "По данным SensorTower: 1,200 downloads"}],
                "confidence": {"level": "high", "rationale": "ok"},
            },
            {
                "id": "CLM-002",
                "type": "fact",
                "statement": "Загрузок 1 200.",
                "evidence": [{"source": "SRC-001", "quote": "По отчёту: всего 1,2 загрузки на пользователя."}],
                "confidence": {"level": "high", "rationale": "ok"},
            },
            {
                "id": "CLM-003",
                "type": "fact",
                "statement": "По данным SensorTower: 1,200 downloads.",
                "evidence": [{"source": "SRC-001", "quote": "По данным SensorTower: 500 downloads."}],
                "confidence": {"level": "high", "rationale": "ok"},
            },
        ],
    )
    amb_items = EV.check(proj_amb)
    amb_by_id = {(cid, f["code"]): f for f in amb_items for cid in f["ids"]}
    assert amb_by_id[("CLM-001", "evidence_locale_ambiguous")]["status"] == "not_assessed"
    assert amb_by_id[("CLM-001", "evidence_single_source")]["status"] == "warn"
    assert amb_by_id[("CLM-002", "evidence_number_not_in_quote")]["status"] == "error"
    assert amb_by_id[("CLM-003", "evidence_number_not_in_quote")]["status"] == "error"

    # 3. single_source не даёт ok при stale / contradicted / неактивном SRC
    proj_sc = _make_proj(
        tmp_path / "sc",
        sources=[
            {"id": "SRC-001", "title": "S", "kind": "dataset", "access": "own", "url": "https://example.com", "retrieved": "2026-11-10", "reliability": "high", "status": "retracted"},
            {"id": "SRC-002", "title": "S2", "kind": "dataset", "access": "own", "url": "https://example.com", "retrieved": "2026-11-10", "reliability": "high", "status": "active"},
        ],
        claims=[
            {
                "id": "CLM-001",
                "type": "fact",
                "statement": "Конверсия 10%.",
                "evidence": [{"source": "SRC-001", "quote": "Конверсия 10%."}],
                "contradicted_by": ["CLM-002"],
                "confidence": {"level": "high", "rationale": "ok"},
            },
            {
                "id": "CLM-002",
                "type": "fact",
                "statement": "Конверсия 5%.",
                "evidence": [{"source": "SRC-002", "quote": "Конверсия 5%."}],
                "contradicted_by": ["CLM-001"],
                "confidence": {"level": "high", "rationale": "ok"},
            },
        ],
    )
    sc_items = EV.check(proj_sc)
    for cid in ("CLM-001", "CLM-002"):
        ss = [f for f in sc_items if f["ids"] == [cid] and f["code"] == "evidence_single_source"]
        assert len(ss) == 1
        assert ss[0]["status"] == "warn"
