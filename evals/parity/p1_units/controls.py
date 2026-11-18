"""Предварительно размеченные синтетические случаи для сверки единиц."""
from dataclasses import dataclass
from pathlib import Path
import hashlib

import yaml

UNIT_WARNING = "evidence_unit_mismatch"
FACT = "В марте среди новых клиентов покупатели составили 34% от посетителей."
PERCENT = "Отток снизился на 4%."
POINTS = "Отток снизился на 4 п.п."


@dataclass(frozen=True)
class Case:
    name: str
    statement: str
    quotes: tuple[str, ...]
    units: tuple[str, str] = ()
    legacy: tuple[str, str] = ()
    claim_type: str = "fact"
    derived: bool = False
    csv_locator: bool = False
    additional_locator: bool = False


CASES = (
    Case("historical-percent-to-points", FACT.replace("34%", "34 п.п."), (FACT,), ("п.п.", "%")),
    Case("points-to-percent", PERCENT, (POINTS,), ("%", "п.п.")),
    Case("words-and-decimal", "Отток снизился на 4,0 процентных пункта.",
         ("Отток снизился на 4 процента.",), ("п.п.", "%")),
    Case("decimal-equivalent", "Отток снизился на 4,0\u00a0%.", (PERCENT,)),
    Case("percent-words-equivalent", "Отток снизился на 4 процента.", (PERCENT,)),
    Case("points-words-equivalent", "Отток снизился на 4 процентных пункта.", (POINTS,)),
    Case("points-spaced-equivalent", "Отток снизился на 4 п. п.", (POINTS,)),
    Case("number-without-statement-unit", "Отток снизился на 4.", (PERCENT,)),
    Case("number-without-quote-unit", POINTS, ("Отток снизился на 4.",)),
    Case("unit-separated-from-number", POINTS, ("Единица — %. Отток снизился на 4.",)),
    Case("unit-on-range", "Отток снизился на 4–6 п.п.", ("Отток снизился на 4–6%.",)),
    Case("unit-on-verbal-range", "Отток снизился на 4 п.п.", ("Отток снизился от 2% до 4%.",)),
    Case("repeated-value", "Отток снизился на 4 п.п.", ("Отток снизился на 4% в 4 регионах.",)),
    Case("two-units-one-value", POINTS, ("Отток снизился на 4% / п.п.",)),
    Case("two-unit-values", "Отток снизился на 4 п.п. до 8%.",
         ("Отток снизился на 4% до 8%.",)),
    Case("different-quotes", POINTS, (PERCENT, POINTS)),
    Case("different-metrics", "Доля возвратов — 4%.", ("Отток снизился на 4 п.п.",)),
    Case("signed-values", "Изменение составило −4 п.п.", ("Изменение составило +4%.",)),
    Case("spelled-number-in-quote", POINTS, ("Отток снизился на четыре процента.",)),
    Case("quote-and-other-locator", POINTS, (PERCENT,), additional_locator=True),
    Case("calculated-interpretation", POINTS, (PERCENT,),
         legacy=("evidence_single_source", "warn"), claim_type="interpretation", derived=True),
    Case("calculated-forecast", POINTS, (PERCENT,),
         legacy=("evidence_single_source", "warn"), claim_type="forecast", derived=True),
    Case("csv-only-locator", POINTS, (),
         legacy=("evidence_quote_not_assessed", "not_assessed"), csv_locator=True),
    Case("missing-number", "Отток снизился на 5 п.п.", (PERCENT,),
         legacy=("evidence_number_not_in_quote", "error")),
    Case("ambiguous-locale", "Loss снизился на 4,000 п.п.", ("Loss снизился на 4,000%.",),
         legacy=("evidence_locale_ambiguous", "not_assessed")),
    Case("forecast-as-fact", "Ожидается снижение на 4%.", ("Ожидается снижение на 4%.",),
         legacy=("evidence_forecast_as_fact", "warn")),
    Case("review-link-target", "См. [отчёт](<https://example.test/4 п.п.>).",
         ("См. [отчёт](<https://example.test/4%>).",)),
    Case("review-inline-code", "Тег `4 п.п.`.", ("Тег `4%`.",)),
    Case("review-basis-points", "Снижение: 4 п.п. / б.п.", ("Снижение: 4% / б.п.",)),
    Case("review-basis-points-words", "Снижение: 4 п.п. или базисных пункта",
         ("Снижение: 4% или базисных пункта",)),
    Case("review-list-quote", POINTS, ("1. " + PERCENT,), ("п.п.", "%")),
    Case("review-heading-quote", POINTS, ("## " + PERCENT,), ("п.п.", "%")),
    Case("review-block-quote", POINTS, ("> " + PERCENT,), ("п.п.", "%")),
    Case("review-shared-encoded-url", POINTS + " https://example.test/a%20b",
         (PERCENT + " https://example.test/a%20b",), ("п.п.", "%")),
    Case("review-distinct-source-markup", POINTS + " https://example.test/a%20b",
         (PERCENT + " https://example.test/c%20d",)),
)


def materialize(project: Path, case: Case) -> None:
    source_path = "sources/report.csv" if case.csv_locator else "sources/report.md"
    source = project / source_path
    source.parent.mkdir(parents=True)
    text = "Метрика,Снижение\nОтток,4%\n" if case.csv_locator else "\n".join(case.quotes) + "\n"
    source.write_text(text, encoding="utf-8")

    evidence = [{"source": "SRC-001", "quote": quote} for quote in case.quotes]
    if case.csv_locator or case.additional_locator:
        evidence.append({"source": "SRC-001", "locator": "B2 (снижение 4%)"})
    elif case.name == "historical-percent-to-points":
        evidence[0]["locator"] = "строка 1"

    confidence = {"level": "high", "rationale": "Синтетический контроль."}
    claim = {
        "id": "CLM-001", "type": case.claim_type, "statement": case.statement,
        "confidence": confidence, "evidence": evidence,
    }
    claims = [claim]
    if case.derived:
        claim["derived_from"] = ["CLM-002"]
        claims.append({
            "id": "CLM-002", "type": "fact", "statement": case.quotes[0],
            "confidence": confidence, "evidence": [evidence[0]],
        })

    ledger = project / "state/ledger"
    ledger.mkdir(parents=True)
    for name, records in (
        ("sources", [{
            "id": "SRC-001", "title": "Синтетический отчёт", "access": "own",
            "kind": "table" if case.csv_locator else "document",
            "retrieved": "2026-01-01", "reliability": "high", "path": source_path,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }]),
        ("claims", claims),
    ):
        (ledger / f"{name}.yaml").write_text(
            yaml.safe_dump(records, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
