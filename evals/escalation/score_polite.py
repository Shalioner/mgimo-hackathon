#!/usr/bin/env python3
"""Часть 2 (PROTOCOL.md): словарь до правил вежливого несогласия (1c25ec7) против текущего на sets/polite.json."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools" / "hooks"))
import strategist_hook as H  # noqa: E402  (QUOTED и текущий словарь)


def old_lexicon(rev="1c25ec7"):
    src = subprocess.run(["git", "show", f"{rev}:tools/hooks/objection_lexicon.py"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", check=True).stdout
    f = HERE / "runs" / f"lexicon_{rev}.py"
    f.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"lex_{rev}", f)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    items = json.loads((HERE / "sets" / "polite.json").read_text(encoding="utf-8"))["items"]
    old = old_lexicon()
    res = {}
    for tag, det in (("до (1c25ec7)", lambda t: old.detect(H.QUOTED.sub(" ", t))), ("сейчас", H.detect_objection)):
        by = {}
        rows = []
        for it in items:
            hit = bool(det(it["text"]))
            by.setdefault(it["class"], [0, 0])
            by[it["class"]][0] += hit
            by[it["class"]][1] += 1
            rows.append({"class": it["class"], "hit": hit, "text": it["text"]})
        res[tag] = {"by_class": by, "rows": rows}
        print(tag, ", ".join(f"{c}: {h}/{n}" for c, (h, n) in sorted(by.items())))
    (HERE / "runs" / "polite_scored.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
