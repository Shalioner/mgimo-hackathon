"""Модель решений (System One: Jev, Kev…): запрос, пороги уверенности, пачки, журнал, отсутствие провайдера."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import decide as DC  # noqa: E402
import ledger as L  # noqa: E402

ANSWERS = {
    "CLM-001.type": {"type": "choice", "choice": "fact", "confidence": 0.93,
                     "probabilities": {"fact": 0.93, "hypothesis": 0.05, "forecast": 0.01, "recommendation": 0.01}},
    "CLM-001.quantified": {"type": "noul", "noul": 0.97},
    "CLM-002.type": {"type": "choice", "choice": "hypothesis", "confidence": 0.52,
                     "probabilities": {"fact": 0.4, "hypothesis": 0.52, "forecast": 0.04, "recommendation": 0.04}},
    "CLM-002.quantified": {"type": "refusal"},
}


@pytest.fixture
def server():
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["path"] = self.path
            got["auth"] = self.headers.get("Authorization")
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = json.dumps({"id": "dec-1", "model": "jev-test", "answers": ANSWERS,
                               "usage": {"input_tokens": 120, "output_tokens": 0, "cost": 0.000005}}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, got
    srv.shutdown()


@pytest.fixture
def setup(tmp_path, monkeypatch, server):
    srv, got = server
    loc = tmp_path / "routing.local.yaml"
    loc.write_text("decision:\n  providers:\n"
                   f"    - {{name: kev-local, base_url: 'http://127.0.0.1:{srv.server_port}/v1', model: kev-test, "
                   "api_key_env: DEC_TEST_KEY}\n", encoding="utf-8")
    monkeypatch.setattr(L, "ROUTING_LOCAL_PATH", loc)
    monkeypatch.setenv("DEC_TEST_KEY", "k-dec")
    p = tmp_path / "p"
    L.main(["init", str(p), "--title", "Д"])
    items = tmp_path / "items.yaml"
    items.write_text("- {id: CLM-001, text: 'Синхронизация — 34% обращений'}\n- {id: CLM-002, text: 'Пользователи уйдут'}\n",
                     encoding="utf-8")
    return p, items, got


def test_batch_request_follows_system_one_and_flags_doubtful(setup, capsys):
    p, items, got = setup
    q = ROOT / "knowledge/decisions/claim_type.yaml"
    assert DC.main(["--project", str(p), "--questions", str(q), "--items", str(items), "--step", "3"]) == 0
    assert got["path"] == "/v1/systemone" and got["auth"] == "Bearer k-dec"
    body = got["body"]
    assert set(body) == {"model", "state", "questions"} and body["model"] == "kev-test"
    assert set(body["questions"]) == set(ANSWERS)  # одна пачка: 2 элемента × 2 вопроса
    assert "34% обращений" in body["questions"]["CLM-001.type"]["instructions"]
    assert set(body["questions"]["CLM-001.type"]["criteria"]) == {"fact", "hypothesis", "forecast", "recommendation"}
    out = capsys.readouterr().out
    assert "На проверку T1 (только эти, не весь список): CLM-002.type, CLM-002.quantified" in out
    saved = json.loads(next((p / "state/delegated").glob("*_decide_claim_type.json")).read_text(encoding="utf-8"))
    assert set(saved["needs_review"]) == {"CLM-002.type", "CLM-002.quantified"}
    log = (p / "state/run_log.md").read_text(encoding="utf-8")
    assert "decision:kev-test" in log and "уверенно 2, на проверку T1 2" in log and "k-dec" not in log


def test_no_provider_means_fallback_to_t1(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("REQUESTY_API_KEY", raising=False)
    monkeypatch.setattr(L, "ROUTING_LOCAL_PATH", tmp_path / "none.yaml")
    p = tmp_path / "p"
    L.main(["init", str(p), "--title", "Д"])
    q = ROOT / "knowledge/decisions/route_tier.yaml"
    assert DC.main(["--project", str(p), "--questions", str(q), "--state", "сравнить 4 варианта", "--no-emulate"]) == 3


def test_provider_with_key_in_env_is_picked(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    cfg = L.load_routing(Path("/nonexistent.yaml"))
    assert DC.pick_provider(cfg)["model"] == "typesafe/jev-1.13"


@pytest.mark.parametrize("ans,doubt", [
    ({"type": "noul", "noul": 0.9}, False), ({"type": "noul", "noul": 0.1}, False),
    ({"type": "noul", "noul": 0.6}, True), ({"type": "refusal"}, True),
    ({"type": "score", "score": 2.0, "confidence": 0.8}, False), ({"type": "choice", "confidence": 0.5}, True)])
def test_doubtful(ans, doubt):
    assert bool(DC.doubtful(ans, 0.75)) is doubt


def test_question_files_are_valid():
    files = list((ROOT / "knowledge/decisions").rglob("*.yaml"))
    assert len(files) >= 10
    for f in files:
        assert DC.load_questions(f), f


@pytest.fixture
def no_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("REQUESTY_API_KEY", raising=False)
    monkeypatch.setattr(L, "ROUTING_LOCAL_PATH", tmp_path / "none.yaml")
    p = tmp_path / "p"
    L.main(["init", str(p), "--title", "Д"])
    return p


def fake_delegate(reply, seen):
    def run(argv):
        args = dict(zip(argv[::2], argv[1::2]))
        seen["prompt"] = Path(args["--prompt-file"]).read_text(encoding="utf-8")
        seen["task"] = args["--task"]
        out = Path(args["--project"]) / "state/delegated/20990101-000000_classify.md"
        out.write_text(reply, encoding="utf-8")
        return 0
    return run


def test_emulation_on_cheap_llm_when_no_jev(no_provider, monkeypatch, capsys):
    import delegate as D
    seen = {}
    monkeypatch.setattr(D, "main", fake_delegate(
        '```json\n{"answers": {"q": {"choice": "T3", "sure": true, "quote": "сравнить 4 варианта"}}}\n```', seen))
    assert DC.main(["--project", str(no_provider), "--ask", "Какой уровень нужен?", "--options", "T1,T2,T3",
                    "--state", "сравнить 4 варианта стратегии", "--step", "2"]) == 0
    assert seen["task"] == "classify" and "дословная цитата" in seen["prompt"] and "сравнить 4 варианта" in seen["prompt"]
    out = capsys.readouterr().out
    assert "T3 (0.90)" in out and "на проверку" not in out.split("Сохранено")[0]  # уверен + цитата найдена
    assert not list((no_provider / "state/delegated").glob("_decide_prompt_*"))  # временное задание убрано


def test_labels_confidence_comes_from_verified_quote():
    qs = {"a": {"type": "noul"}, "b": {"type": "noul"}, "c": {"type": "noul"},
          "d": {"type": "choice", "criteria": {"x": "", "y": ""}}, "e": {"type": "score", "criteria": ["н", "с", "в"]}}
    state = "## Главное\nВывод: **оставить как есть** нельзя — удержание 24% [CLM-001]."
    raw = {"a": {"verdict": "yes", "quote": "Оставить как есть нельзя"},  # разметка и регистр не мешают
           "b": {"verdict": "yes", "quote": "выдуманная цитата, которой нет"},
           "c": {"verdict": "no"},
           "d": {"choice": "z", "sure": True, "quote": "Главное"},
           "e": {"level": 2, "sure": True, "quote": "удержание 24% [CLM-001]"}}
    out = DC.from_labels(raw, qs, state)
    assert out["a"]["noul"] == 0.95 and out["b"]["noul"] == 0.6 and out["c"]["noul"] == 0.08
    assert out["d"] == {"type": "refusal"}  # вариант не из criteria
    assert out["e"]["score"] == 2.0 and out["e"]["confidence"] == 0.9
    assert DC.doubtful(out["b"], 0.85) and not DC.doubtful(out["a"], 0.85) and not DC.doubtful(out["c"], 0.85)


def test_code_check():
    text = "## Главное\nALT-001, ALT-002, ALT-002, ALT-003\nОставить как есть"
    assert DC.code_check({"all": [r"^##\s*Главное"]}, text) is True
    assert DC.code_check({"min_count": {r"ALT-\d{3}": 4}}, text) is False  # уникальных три
    assert DC.code_check({"any": ["закрыть", "оставить как есть"]}, text) is True
    assert DC.code_check({"none": ["TODO"]}, text) is True


def test_multi_gate_code_first_skips_model_for_decided(no_provider, monkeypatch, capsys):
    import delegate as D
    out_dir = no_provider / "outputs"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "03_situation.md").write_text("# Ситуация\nНеизвестно: доля.\n", encoding="utf-8")  # нет «Главное»
    (out_dir / "07_validation_plan.md").write_text("# План\n| EXP |\n", encoding="utf-8")  # нет Stop / Go
    seen = {}
    monkeypatch.setattr(D, "main", fake_delegate('{"answers": {}}', seen))
    rc = DC.main(["--project", str(no_provider), "--gate", str(out_dir / "03_situation.md"),
                  str(out_dir / "07_validation_plan.md")])
    out = capsys.readouterr().out
    assert rc == 4 and "03.main_block" in out and "07.stop_go" in out and "07.baseline" in out
    assert "03.main_block" not in seen["prompt"] and "07.stop_go" not in seen["prompt"]  # решено кодом
    assert "03.types_marked" in seen["prompt"]  # по существу — модели
    assert "=== 03_situation.md ===" in seen["prompt"] and "=== 07_validation_plan.md ===" in seen["prompt"]


def test_emulation_bad_json_is_error(no_provider, monkeypatch):
    import delegate as D
    monkeypatch.setattr(D, "main", fake_delegate("не знаю", {}))
    assert DC.main(["--project", str(no_provider), "--ask", "Готово?", "--state", "x"]) == 1


def test_gate_reports_fixes_and_exit_4(no_provider, monkeypatch, capsys):
    import delegate as D
    art = no_provider / "outputs" / "04_options.md"
    art.parent.mkdir(exist_ok=True)
    art.write_text("# Варианты\n| | ALT-001 | ALT-002 |\n", encoding="utf-8")
    reply = {"answers": {"main_block": {"type": "noul", "noul": 0.05}, "three_distinct": {"type": "noul", "noul": 0.1},
                         "status_quo_kill": {"type": "noul", "noul": 0.02}, "real_objection": {"type": "noul", "noul": 0.9},
                         "same_criteria": {"type": "noul", "noul": 0.95}, "human_decides": {"type": "noul", "noul": 0.97}}}
    import json as J
    monkeypatch.setattr(D, "main", fake_delegate(J.dumps(reply), {}))
    assert DC.main(["--project", str(no_provider), "--gate", str(art)]) == 4  # нет «Главное» и ALT — решено кодом
    out = capsys.readouterr().out
    assert "НЕ ГОТОВО" in out and "Оставить как есть" in out and "real_objection" not in out.split("Исправить:")[1]


def test_ask_builds_typed_questions():
    assert DC.ask_question("Готово?", None, None) == {"type": "noul", "instructions": "Готово?"}
    assert DC.ask_question("Кто?", "a, b", None)["criteria"] == {"a": "a", "b": "b"}
    assert DC.ask_question("Насколько?", None, "низко,высоко")["criteria"] == ["низко", "высоко"]


def test_bad_questions_rejected(tmp_path):
    f = tmp_path / "q.yaml"
    f.write_text("questions:\n  x: {type: score, criteria: {a: b}}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        DC.load_questions(f)


def test_route_mentions_decision_model(capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("REQUESTY_API_KEY", raising=False)
    assert L.main(["route", "classify"]) == 0
    assert "tools/decide.py" in capsys.readouterr().out
