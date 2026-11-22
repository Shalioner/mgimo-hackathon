import json
from pathlib import Path
import runpy


EVAL = Path(__file__).resolve().parents[1] / "evals/score9/paired-workshop"


def test_cost_uses_the_last_usage_snapshot_per_chat():
    measure = runpy.run_path(str(EVAL / "export_pair.py"))["measure"]
    modes = json.loads((EVAL / "metrics.json").read_text(encoding="utf-8"))["modes"]
    for name, expected in (("plain", "0.3836"), ("strategist", "1.4439")):
        turns = modes[name]["turns"]
        total = measure(turns)
        assert total == modes[name]["total"]
        assert total["api_equivalent_usd"] == expected
        assert total["output"] == turns[3]["usage"][0]["output_tokens"] + turns[4]["usage"][0]["output_tokens"]
