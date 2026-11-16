"""No model dependencies or downloads: schema, integrity, limits, process policy."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
import local_decision as L

Q = {"tier": {"type": "choice", "instructions": "Уровень?", "criteria": {"T1": "просто", "T2": "сложно"}}}
RESULT = {"answers": {"tier": {"type": "choice", "choice": "T1", "confidence": 0.8,
                               "probabilities": {"T1": 0.8, "T2": 0.2}}},
          "usage": {"input_tokens": 10, "output_tokens": 0}}


def test_existing_routing_questions():
    q = L.read_questions(Path(__file__).resolve().parents[1] / "knowledge/decisions/route_tier.yaml")
    L.validate_request({"запрос": "Сравни стратегии"}, q)


@pytest.mark.parametrize("state,q", [("x" * 8193, Q), ("x", {}), ("x", {str(i): Q["tier"] for i in range(9)}),
                                     ("x", {"q": {**Q["tier"], "instructions": {"type": "audio", "path": "secret"}}}),
                                     ("x", {"q": {**Q["tier"], "multiple": "yes"}}),
                                     ("x", {"q": {**Q["tier"], "criteria": ["T1", "T2"]}}),
                                     ("x", {"q": {**Q["tier"], "type": []}}),
                                     (float("nan"), Q)])
def test_bad_input_before_model(state, q):
    with pytest.raises(ValueError):
        L.validate_request(state, q)


@pytest.mark.parametrize("edit", [
    lambda r: r["answers"]["tier"].update(choice="T4"),
    lambda r: r["answers"]["tier"].update(choice=[]),
    lambda r: r["answers"]["tier"].update(confidence=float("nan")),
    lambda r: r["answers"]["tier"]["probabilities"].update(T1=-1),
    lambda r: r["answers"]["tier"]["probabilities"].update(T1=0.3),
    lambda r: r["answers"].update(extra={}),
    lambda r: r["usage"].update(output_tokens=2),
    lambda r: r["usage"].update(input_tokens=True),
])
def test_invalid_result(edit):
    r = copy.deepcopy(RESULT)
    edit(r)
    with pytest.raises(ValueError):
        L.validate_result(r, Q)


def test_valid_distribution():
    L.validate_result(RESULT, Q)
    q = {"yes": {"type": "noul", "instructions": "Есть?"}}
    L.validate_result({"answers": {"yes": {"type": "noul", "noul": 0.1}}, "usage": RESULT["usage"]}, q)


def test_manifest_rejects_modified_custom_code(tmp_path, monkeypatch):
    weights = tmp_path / "weights"
    weights.mkdir()
    code = weights / "model.py"
    code.write_text("reviewed code", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"model.py": hashlib.sha256(code.read_bytes()).hexdigest()}), encoding="utf-8")
    monkeypatch.setattr(L, "MANIFEST", manifest)
    L.verify_weights(weights)
    code.write_text("different code", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256"):
        L.verify_weights(weights)


def test_yaml_error_is_structured(tmp_path):
    q = tmp_path / "q.yaml"
    q.write_text("questions: [unfinished", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid questions YAML"):
        L.read_questions(q)


@pytest.mark.parametrize("body,code,success", [(json.dumps({"status": "succeeded", "results": []}), 0, True),
                                              ('{"status":"blocked","reason":"MPS error"}', 3, False),
                                              ('not json', 0, False)])
def test_supervised_offline_child_once(tmp_path, monkeypatch, body, code, success):
    monkeypatch.setattr(L, "LAB", tmp_path)
    monkeypatch.setattr(L.sys, "platform", "darwin")
    monkeypatch.setattr(L, "memory", lambda: {"swap_used_mib": 100})
    calls = []
    class Process:
        returncode = None
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
        def communicate(self, **kwargs):
            self.returncode = code
            return body, ""
        def poll(self):
            return self.returncode
    monkeypatch.setattr(L.subprocess, "Popen", Process)
    monkeypatch.setenv("HF_TOKEN", "DO_NOT_INHERIT")
    r = L.execute([{"id": "test", "state": "x", "questions": Q}], tmp_path, 10)
    assert (r["status"] == "succeeded") == success and len(calls) == 1
    command, kwargs = calls[0]
    assert "(deny network*)" in command[2]
    assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
    assert "HF_TOKEN" not in kwargs["env"]
    assert not any("WATERMARK" in k for k in kwargs["env"])
    assert r["process_finished"]


@pytest.mark.parametrize("guard", ["rss", "pressure", "swap"])
def test_memory_guard_kills_only_own_process(tmp_path, monkeypatch, guard):
    monkeypatch.setattr(L, "LAB", tmp_path)
    monkeypatch.setattr(L.sys, "platform", "darwin")
    samples = iter([{"swap_used_mib": 100, "pressure_level": 1},
                    {"swap_used_mib": 4000 if guard == "swap" else 100,
                     "pressure_level": 4 if guard == "pressure" else 1}])
    monkeypatch.setattr(L, "memory", lambda: next(samples, {"swap_used_mib": 100, "pressure_level": 1}))
    events = []
    class Process:
        returncode = None
        pid = 123
        def __init__(self, *a, **kw):
            events.append("spawn")
        def communicate(self, **kwargs):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("fake", 5)
            return "", ""
        def kill(self):
            events.append("kill-own")
            self.returncode = -9
        def poll(self):
            return self.returncode
    monkeypatch.setattr(L.subprocess, "Popen", Process)
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, str(7*1024*1024 if guard == "rss" else 100), ""))
    r = L.execute([{"id": "test", "state": "x", "questions": Q}], tmp_path, 10)
    assert r["status"] == "blocked" and events == ["spawn", "kill-own"]


@pytest.mark.parametrize('edit', [
    lambda a: a.update(choice='T2'),
    lambda a: a.update(confidence=0.7),
])
def test_inconsistent_choice_rejected(edit):
    r = copy.deepcopy(RESULT)
    edit(r['answers']['tier'])
    with pytest.raises(ValueError, match='disagrees'):
        L.validate_result(r, Q)


def test_score_median_is_not_argmax():
    q = {'s': {'type': 'score', 'instructions': '?', 'criteria': ['low', 'mid', 'high']}}
    a = {'type': 'score', 'score': 1, 'confidence': .4,
         'probabilities': {'0': .4, '1': .35, '2': .25}}
    r = {'answers': {'s': a}, 'usage': RESULT['usage']}
    L.validate_result(r, q)
    a['score'] = 0
    with pytest.raises(ValueError, match='median'):
        L.validate_result(r, q)
    a.update(score=1, confidence=.35)
    with pytest.raises(ValueError, match='Confidence'):
        L.validate_result(r, q)


def test_multiple_independent_threshold_and_duplicates():
    q = copy.deepcopy(Q)
    q['tier']['multiple'] = True
    a = {'type': 'choice', 'choices': ['T1', 'T2'], 'probabilities': {'T1': .5, 'T2': .8}}
    r = {'answers': {'tier': a}, 'usage': RESULT['usage']}
    L.validate_result(r, q)  # independent probabilities need not sum to one
    for bad in [['T2'], ['T1', 'T2', 'T2']]:
        a['choices'] = bad
        with pytest.raises(ValueError, match='disagree'):
            L.validate_result(r, q)


@pytest.mark.parametrize('pressure', [2, 4, None])
def test_preflight_refuses_non_normal_pressure(tmp_path, monkeypatch, pressure):
    monkeypatch.setattr(L, 'LAB', tmp_path)
    monkeypatch.setattr(L.sys, 'platform', 'darwin')
    monkeypatch.setattr(L, 'memory', lambda: {'swap_used_mib': 4000, 'pressure_level': pressure})
    monkeypatch.setattr(L.subprocess, 'Popen', lambda *a, **k: pytest.fail('Must not start'))
    report = L.execute([{'id': 'x', 'state': 'x', 'questions': Q}], tmp_path, 10, preflight_samples=3)
    assert report['status'] == 'blocked' and report['process_started'] is False


def test_preflight_checks_all_samples_and_final_race(tmp_path, monkeypatch):
    monkeypatch.setattr(L, 'LAB', tmp_path)
    monkeypatch.setattr(L.sys, 'platform', 'darwin')
    values = iter([1, 1, 1, 4])
    monkeypatch.setattr(L, 'memory', lambda: {'swap_used_mib': 4000, 'pressure_level': next(values)})
    monkeypatch.setattr(L.time, 'sleep', lambda _: None)
    monkeypatch.setattr(L.subprocess, 'Popen', lambda *a, **k: pytest.fail('Must not start'))
    report = L.execute([{'id': 'x', 'state': 'x', 'questions': Q}], tmp_path, 10, preflight_samples=3)
    assert len(report['preflight']) == 3 and report['process_started'] is False
