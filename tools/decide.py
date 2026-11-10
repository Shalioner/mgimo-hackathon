#!/usr/bin/env python3
"""Решение-классификатор (decision model): выбор из вариантов, оценка по шкале, да/нет — без генерации текста.

ТЗ, раздел «Маршрутизация»: «приветствуются специализированные модели, которые выбирают из вариантов,
классифицируют или оценивают по заданным признакам (например, Jev)». Такие модели отвечают вероятностями
за доли цента, поэтому массовые мелкие решения не нужно отдавать LLM:
тип утверждения, поддерживает ли цитата утверждение, к какому вопросу относится источник, уровень задачи.

    python tools/decide.py --project projects/<case> --questions knowledge/decisions/claim_type.yaml \
        --input projects/<case>/state/delegated/claims.md --step 3 [--min-confidence 0.75] [--dry-run]

Протокол — System One (POST {base_url}/systemone; поля model, state, questions). Его отдают TypeSafe Jev
напрямую и через шлюзы (OpenRouter, Requesty, ngrok), а также открытые модели на своём сервере (например, Kev),
поэтому провайдер меняется одной строкой в routing.yaml → decision.providers.

Экономия держится на правиле «уверенно — код, сомнительно — LLM»: ответы с confidence ниже порога, отказ
модели (refusal) и «да/нет» около 0,5 помечаются «на проверку T1» — их и только их разбирает LLM.
Код выхода: 0 — готово; 3 — провайдер не настроен (выполнить задачу на T1, как раньше); 1 — ошибка вызова.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger as L  # noqa: E402

TYPES = {"noul", "choice", "score"}


def load_questions(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    qs = data.get("questions", data)
    if not isinstance(qs, dict) or not qs:
        raise ValueError(f"{path}: нужен словарь questions: {{id: {{type, instructions, criteria}}}}")
    for qid, q in qs.items():
        if not isinstance(q, dict) or q.get("type") not in TYPES:
            raise ValueError(f"{path}: вопрос {qid} — type должен быть одним из {sorted(TYPES)}")
        if q["type"] in ("choice", "score") and not q.get("criteria"):
            raise ValueError(f"{path}: вопрос {qid} ({q['type']}) требует criteria")
        if q["type"] == "score" and not isinstance(q["criteria"], list):
            raise ValueError(f"{path}: вопрос {qid} (score) — criteria списком от низшего к высшему")
    return {k: {f: v for f, v in q.items() if f in ("type", "instructions", "criteria")} for k, q in qs.items()}


def expand_items(questions: dict, items: List[dict]) -> dict:
    """Пачка: каждый вопрос-шаблон применяется к каждому элементу ({id, text}); {text} в instructions
    подставляется. ID ответа — «<id элемента>.<вопрос>». Один вызов вместо N — накладные расходы делятся."""
    out = {}
    for it in items:
        if not isinstance(it, dict) or not it.get("id") or "text" not in it:
            raise ValueError("элемент --items должен быть {id, text}")
        for qid, q in questions.items():
            q2 = dict(q)
            if isinstance(q2.get("instructions"), str):
                q2["instructions"] = q2["instructions"].replace("{text}", str(it["text"]))
            out[f"{it['id']}.{qid}"] = q2
    return out


def pick_provider(cfg: dict, requested: Optional[str] = None) -> Optional[dict]:
    """Первый провайдер, у которого есть адрес и (если нужен) ключ в окружении."""
    for p in (cfg.get("decision") or {}).get("providers") or []:
        if requested and p.get("name") != requested:
            continue
        url = str(p.get("base_url") or "")
        if not url.startswith(("http://", "https://")) or not p.get("model"):
            continue
        if p.get("api_key_env") and not os.environ.get(p["api_key_env"]):
            continue
        return p
    return None


def call(provider: dict, body: dict, timeout: int) -> Tuple[int, dict, str]:
    req = urllib.request.Request(provider["base_url"].rstrip("/") + "/systemone",
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    key = os.environ.get(provider.get("api_key_env") or "", "")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 0, json.loads(r.read().decode("utf-8")), ""
    except urllib.error.HTTPError as exc:
        return exc.code, {}, exc.read().decode("utf-8", errors="replace")[:500]
    except (OSError, ValueError) as exc:
        return 1, {}, str(exc)


def doubtful(ans: dict, min_conf: float) -> Optional[str]:
    """Причина отправить ответ на проверку LLM или None, если ответу можно верить."""
    t = ans.get("type")
    if t == "refusal":
        return "модель отказалась отвечать"
    if t == "noul":
        p = float(ans.get("noul", 0.5))
        return f"да/нет неуверенно (p={p:.2f})" if 1 - min_conf < p < min_conf else None
    if t in ("choice", "score"):
        c = float(ans.get("confidence", 0))
        return f"confidence {c:.2f} < {min_conf}" if c < min_conf else None
    return f"неизвестный тип ответа {t}"


def value(ans: dict, question: dict) -> str:
    t = ans.get("type")
    if t == "noul":
        return f"да ({ans['noul']:.2f})" if ans.get("noul", 0) >= 0.5 else f"нет ({ans.get('noul', 0):.2f})"
    if t == "choice":
        return f"{ans.get('choice')} ({ans.get('confidence', 0):.2f})"
    if t == "score":
        crit = question.get("criteria") or []
        i = int(round(float(ans.get("score", 0))))
        label = crit[i] if 0 <= i < len(crit) else ans.get("score")
        return f"{label} (score {ans.get('score')}, {ans.get('confidence', 0):.2f})"
    return t or "?"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Классификация/оценка моделью решений (System One: Jev, Kev…)")
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--questions", type=Path, required=True, help="YAML с вопросами (knowledge/decisions/*.yaml)")
    ap.add_argument("--input", type=Path, action="append", default=[], help="файл-данные (state); можно несколько")
    ap.add_argument("--state", help="короткий state строкой вместо файлов")
    ap.add_argument("--items", type=Path, help="YAML-список {id, text}: вопросы применяются к каждому элементу одним вызовом")
    ap.add_argument("--step", default="-")
    ap.add_argument("--provider", help="имя провайдера из routing.yaml → decision.providers")
    ap.add_argument("--min-confidence", type=float, default=None)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = L.load_routing()
    project = args.project.resolve()
    if not (project / "state" / "run_log.md").exists():
        print(f"Нет проекта {project} (ожидается state/run_log.md)", file=sys.stderr)
        return 2
    try:
        questions = load_questions(args.questions)
        if args.items:
            questions = expand_items(questions, yaml.safe_load(args.items.read_text(encoding="utf-8")) or [])
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(exc, file=sys.stderr)
        return 2
    parts = [args.state] if args.state else []
    for inp in args.input:
        f = inp if inp.is_absolute() else Path.cwd() / inp
        if not f.exists():
            print(f"Нет входного файла {f}", file=sys.stderr)
            return 2
        parts.append(f.read_text(encoding="utf-8"))
    if not parts and args.items:
        parts = ["Оцени каждый элемент по его вопросам; текст элемента приведён в instructions вопроса."]
    if not parts:
        print("Нужен --input, --state или --items", file=sys.stderr)
        return 2
    min_conf = args.min_confidence or float((cfg.get("decision") or {}).get("min_confidence", 0.75))

    provider = pick_provider(cfg, args.provider)
    if not provider:
        print("Модель решений не настроена (routing.yaml → decision.providers: нет адреса или ключа в окружении). "
              "Выполните задачу на T1 (`ledger.py route classify`) и запишите это в журнал.", file=sys.stderr)
        return 3
    body = {"model": provider["model"], "state": "\n\n".join(parts), "questions": questions}
    if args.dry_run:
        print(f"[dry-run] {provider['name']} · {provider['model']} · {provider['base_url'].rstrip('/')}/systemone · "
              f"вопросов {len(questions)}, state {len(body['state'])} символов, порог {min_conf}")
        return 0

    t0 = time.monotonic()
    code, data, err = call(provider, body, args.timeout)
    secs = round(time.monotonic() - t0, 1)
    u = data.get("usage") or {}
    usage = f"вход {u.get('input_tokens', 0)} ток." + (f", ${u['cost']:.6f}" if u.get("cost") is not None else "")
    answers: Dict[str, dict] = data.get("answers") or {}
    if code != 0 or not answers:
        L.cmd_log(project, args.step, f"решения {args.questions.stem}", "T1", f"decision:{provider['model']}",
                  f"ошибка (код {code}): {err.strip()[-200:] or 'нет answers'}", f"{secs} с")
        print(f"Вызов модели решений не удался (код {code}): {err[:300]}. Выполните задачу на T1.", file=sys.stderr)
        return 1

    review = {qid: why for qid, a in answers.items() if (why := doubtful(a, min_conf))}
    out_dir = project / "state" / "delegated"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"{stamp}_decide_{args.questions.stem}.json"
    out.write_text(json.dumps({"provider": provider["name"], "model": data.get("model", provider["model"]),
                               "min_confidence": min_conf, "answers": answers, "needs_review": review,
                               "usage": u}, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = out.relative_to(project)
    L.cmd_log(project, args.step, f"решения {args.questions.stem}", "T1", f"decision:{provider['model']}",
              f"ok → {rel}; уверенно {len(answers) - len(review)}, на проверку T1 {len(review)}", f"{secs} с; {usage}")
    for qid, a in answers.items():
        mark = f"  → на проверку T1: {review[qid]}" if qid in review else ""
        print(f"- {qid}: {value(a, questions.get(qid, {}))}{mark}")
    if review:
        print("На проверку T1 (только эти, не весь список): " + ", ".join(review))
    print(f"Сохранено: {rel} ({secs} с; {usage}). Уверенные ответы можно вносить; "
          f"{len(review)} — проверить моделью T1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
