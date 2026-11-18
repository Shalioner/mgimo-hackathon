#!/usr/bin/env python3
"""M08: Детерминированный расчёт экономической модели (read-only).

Читает YAML с драйверами (low / base / high, type, ref) и формулой,
вычисляет сценарии (pessimistic / base / optimistic), точку безубыточности,
ранжирование чувствительности (tornado + топ-3), условие смены решения
и формирует черновик производного утверждения CLM (type: forecast)
с derived_from — без записи в state/ledger/.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import yaml

_REF_RE = re.compile(r"^(?:SRC|CLM|HYP|PAIN|ALT|DEC|PRM|EXP|CHG)-\d{3}$")
BREAKEVEN_VALUE_FORMAT = ".6g"

_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a ** b,
}


def _eval_ast(node: ast.AST, env: Dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body, env)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"Недопустимая константа в формуле: {node.value!r}")
        val = float(node.value)
        if not math.isfinite(val):
            raise ValueError(f"Неконечная константа в формуле: {node.value!r}")
        return val
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ValueError(f"Неизвестный драйвер в формуле: {node.id}")
        val = float(env[node.id])
        if not math.isfinite(val):
            raise ValueError(f"Неконечное значение драйвера {node.id}: {val!r}")
        return val
    if isinstance(node, ast.UnaryOp):
        val = _eval_ast(node.operand, env)
        if isinstance(node.op, ast.UAdd):
            res = +val
        elif isinstance(node.op, ast.USub):
            res = -val
        else:
            raise ValueError(f"Недопустимая унарная операция: {type(node.op).__name__}")
        if not math.isfinite(res):
            raise ValueError("Неконечный результат унарной операции в формуле")
        return res
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_BINOPS:
            raise ValueError(f"Недопустимая операция в формуле: {op_type.__name__}")
        left = _eval_ast(node.left, env)
        right = _eval_ast(node.right, env)
        if op_type is ast.Div and abs(right) == 0.0:
            raise ZeroDivisionError("Деление на ноль при расчёте экономической модели")
        if op_type is ast.Pow and abs(right) > 20:
            raise ValueError("Степень в формуле превышает допустимый предел (±20)")
        try:
            raw_res = _ALLOWED_BINOPS[op_type](left, right)
        except OverflowError as exc:
            raise ValueError("Переполнение при вычислении арифметики в формуле") from exc
        if isinstance(raw_res, complex):
            raise ValueError("Комплексный результат при вычислении формулы (например, дробная степень отрицательного числа)")
        try:
            val = float(raw_res)
        except (TypeError, OverflowError) as exc:
            raise ValueError(f"Нечисловой результат в формуле: {exc}") from exc
        if not math.isfinite(val):
            raise ValueError("Неконечный результат (inf/nan) при расчёте экономической модели")
        return val
    raise ValueError(f"Запрещённый элемент синтаксиса в формуле: {type(node).__name__}")


def _compile_formula(formula: str, allowed_names: set[str]) -> ast.Expression:
    try:
        tree = ast.parse(formula.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Синтаксическая ошибка в формуле '{formula}': {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise ValueError(f"Переменная '{node.id}' в формуле не описана в drivers")
        if not isinstance(
            node,
            (
                ast.Expression,
                ast.BinOp,
                ast.UnaryOp,
                ast.Constant,
                ast.Name,
                ast.Load,
                ast.Add,
                ast.Sub,
                ast.Mult,
                ast.Div,
                ast.Pow,
                ast.UAdd,
                ast.USub,
            ),
        ):
            raise ValueError(f"Запрещённая конструкция в формуле: {type(node).__name__}")
    return tree


def _normalize_drivers(raw_drivers: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_drivers, list) or not raw_drivers:
        raise ValueError("Поле 'drivers' должно быть непустым списком")

    drivers: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_drivers):
        if not isinstance(item, dict):
            raise ValueError(f"Драйвер #{idx + 1} должен быть словарём")
        did = str(item.get("id") or item.get("name") or "").strip()
        if not did or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", did):
            raise ValueError(f"Некорректный id драйвера #{idx + 1}: {did!r}")
        if did in seen:
            raise ValueError(f"Дублирующийся id драйвера: {did}")
        seen.add(did)

        for bound in ("low", "base", "high"):
            if bound not in item:
                raise ValueError(f"У драйвера {did} отсутствует '{bound}'")
        low = float(item["low"])
        base = float(item["base"])
        high = float(item["high"])
        if not (math.isfinite(low) and math.isfinite(base) and math.isfinite(high)):
            raise ValueError(f"Нечисловые границы у драйвера {did}")

        dtype = str(item.get("type") or "assumption").strip().lower()
        if dtype not in {"fact", "assumption", "hypothesis"}:
            raise ValueError(f"Недопустимый type '{dtype}' у драйвера {did}")

        refs_raw: List[str] = []
        if item.get("ref"):
            refs_raw.append(str(item["ref"]).strip())
        if isinstance(item.get("refs"), list):
            refs_raw.extend(str(r).strip() for r in item["refs"] if str(r).strip())

        drivers.append(
            {
                "id": did,
                "label": str(item.get("label") or did),
                "low": low,
                "base": base,
                "high": high,
                "type": dtype,
                "refs": refs_raw,
                "unit": str(item.get("unit") or ""),
            }
        )
    return drivers


def _default_formula(drivers: List[Dict[str, Any]]) -> str:
    ids = {d["id"] for d in drivers}
    conv_id = "conversion" if "conversion" in ids else ("conv" if "conv" in ids else None)
    if "base" in ids and conv_id and "price" in ids:
        terms = ["base", conv_id, "price"]
        if "retention" in ids:
            terms.append("retention")
        expr = " * ".join(terms)
        if "cost" in ids:
            expr = f"{expr} - cost"
        return expr
    raise ValueError(
        "Не задана formula, а стандартный набор (base, conversion/conv, price[, retention, cost]) отсутствует"
    )


def _solve_breakeven(
    tree: ast.Expression,
    base_env: Dict[str, float],
    driver: Dict[str, Any],
    target: float,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Return a threshold and whether all successful probes equal the target."""
    did = driver["id"]
    d_min = min(driver["low"], driver["base"], driver["high"])
    d_max = max(driver["low"], driver["base"], driver["high"])
    d_base = driver["base"]

    def _safe_f(x: float) -> Optional[float]:
        env = dict(base_env)
        env[did] = x
        try:
            val = _eval_ast(tree, env) - target
        except (ZeroDivisionError, ValueError, OverflowError):
            return None
        return val if math.isfinite(val) else None

    f0 = _safe_f(0.0)
    f1 = _safe_f(1.0)
    f2 = _safe_f(2.0)

    x_be: Optional[float] = None
    slope_sign: Optional[int] = None

    # Exact closed-form check when model is affine in did
    if f0 is not None and f1 is not None and f2 is not None:
        d1 = f1 - f0
        d2 = f2 - f1
        if abs(d2 - d1) <= 1e-11 * max(1.0, abs(d1), abs(f0), abs(f1)):
            if abs(d1) > 1e-15:
                cand = -f0 / d1
                fcand = _safe_f(cand)
                if fcand is not None and abs(fcand) <= 1e-6 * max(1.0, abs(target)):
                    x_be = cand
                    slope_sign = 1 if d1 > 0 else -1

    # Domain-aware root search + bisection fallback
    if x_be is None:
        pts: List[float] = [driver["low"], d_base, driver["high"]]
        span = d_max - d_min
        if span > 0:
            for i in range(1, 20):
                pts.append(d_min + span * (i / 20.0))

        # Safe domain extensions avoiding blind f(0.0) failure on denominator drivers
        if d_min > 0:
            for factor in (0.5, 0.25, 0.1, 0.01, 1e-3, 1e-4, 1e-6):
                pts.append(d_min * factor)
            pts.extend([1e-9, 0.0])
        else:
            pts.extend([min(-1.0, d_min * 2.0, d_min * 10.0), d_min * 1.5, 0.0])

        if d_max > 0:
            for factor in (1.5, 2.0, 5.0, 10.0, 100.0):
                pts.append(d_max * factor)
        else:
            pts.extend([1.0, 10.0])

        sorted_pts = sorted(set(pts))
        samples: List[Tuple[float, float]] = []
        for pt in sorted_pts:
            val = _safe_f(pt)
            if val is not None:
                samples.append((pt, val))

        probe_vals = [v for v in (f0, f1, f2) if v is not None] + [v for _, v in samples]
        if probe_vals and all(v == 0 for v in probe_vals):
            return None, True

        # Exact sample hit
        for pt, val in samples:
            if abs(val) <= 1e-12:
                x_be = pt
                break

        # Adjacent sign-change brackets, preferring [d_min, d_max] and closest to base
        if x_be is None:
            brackets: List[Tuple[bool, float, float, float, float, float]] = []
            for (x1, y1), (x2, y2) in zip(samples, samples[1:]):
                if y1 * y2 < 0:
                    in_range = not (x2 < d_min or x1 > d_max)
                    mid_dist = abs(0.5 * (x1 + x2) - d_base)
                    brackets.append((not in_range, mid_dist, x1, y1, x2, y2))
            brackets.sort(key=lambda b: (b[0], b[1]))

            for _, _, lo, flo, hi, fhi in brackets:
                ok_bracket = True
                for _ in range(100):
                    mid = 0.5 * (lo + hi)
                    fmid = _safe_f(mid)
                    if fmid is None:
                        ok_bracket = False
                        break
                    if abs(fmid) <= 1e-12 or abs(hi - lo) <= 1e-13 * max(1.0, abs(mid)):
                        break
                    if (flo <= 0 <= fmid) or (flo >= 0 >= fmid):
                        hi = mid
                        fhi = fmid
                    else:
                        lo = mid
                        flo = fmid
                if not ok_bracket:
                    continue
                cand = 0.5 * (lo + hi)
                fcand = _safe_f(cand)
                # Verify candidate is an actual zero, not a pole discontinuity
                if fcand is not None and abs(fcand) <= 1e-5 * max(1.0, abs(target)):
                    x_be = cand
                    break

        if x_be is not None:
            for scale in (1e-6, 1e-4, 1e-2):
                eps = max(1e-9, abs(x_be) * scale)
                fp = _safe_f(x_be + eps)
                fm = _safe_f(x_be - eps)
                if fp is not None and fm is not None and abs(fp - fm) > 0:
                    slope_sign = 1 if fp > fm else -1
                    break
            if slope_sign is None:
                fp = _safe_f(x_be + max(1e-6, abs(x_be) * 1e-4))
                fb = _safe_f(x_be)
                if fp is not None and fb is not None and abs(fp - fb) > 0:
                    slope_sign = 1 if fp > fb else -1

    if x_be is None or not math.isfinite(x_be):
        return None, False

    op = "<" if (slope_sign is None or slope_sign > 0) else ">"
    range_tol = 1e-12 * max(1.0, abs(d_min), abs(d_max))
    in_checked_range = (d_min - range_tol) <= x_be <= (d_max + range_tol)
    if in_checked_range:
        range_status = "in_range"
        threshold_text = f"решение меняется, если {did} {op} {x_be:{BREAKEVEN_VALUE_FORMAT}}"
    else:
        range_status = "below_range" if x_be < d_min else "above_range"
        threshold_text = (
            f"найденный порог {did} = {x_be:{BREAKEVEN_VALUE_FORMAT}} вне диапазона "
            f"[{d_min:{BREAKEVEN_VALUE_FORMAT}}…{d_max:{BREAKEVEN_VALUE_FORMAT}}]"
        )
    return {
        "driver": did,
        "label": driver["label"],
        "target": target,
        "value": x_be,
        "unit": driver["unit"],
        "direction": "below_breakeven" if op == "<" else "above_breakeven",
        "in_checked_range": in_checked_range,
        "checked_range": [d_min, d_max],
        "range_status": range_status,
        "threshold_text": threshold_text,
    }, False


def _fmt_num(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.2f}".replace(",", " ").rstrip("0").rstrip(".")
    return f"{v:.4f}".rstrip("0").rstrip(".")


def compute_model(spec: Dict[str, Any], claim_id: str = "CLM-901") -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError("Входной YAML должен быть объектом")

    drivers = _normalize_drivers(spec.get("drivers"))
    driver_map = {d["id"]: d for d in drivers}
    formula = str(spec.get("formula") or _default_formula(drivers)).strip()
    tree = _compile_formula(formula, set(driver_map.keys()))

    base_env = {d["id"]: d["base"] for d in drivers}
    base_value = _eval_ast(tree, base_env)

    pess_env: Dict[str, float] = {}
    opt_env: Dict[str, float] = {}
    tornado: List[Dict[str, Any]] = []

    for d in drivers:
        did = d["id"]
        env_low = dict(base_env)
        env_low[did] = d["low"]
        env_high = dict(base_env)
        env_high[did] = d["high"]

        val_low = _eval_ast(tree, env_low)
        val_high = _eval_ast(tree, env_high)

        if val_high >= val_low:
            pess_env[did] = d["low"]
            opt_env[did] = d["high"]
        else:
            pess_env[did] = d["high"]
            opt_env[did] = d["low"]

        tornado.append(
            {
                "id": did,
                "label": d["label"],
                "type": d["type"],
                "low_input": d["low"],
                "base_input": d["base"],
                "high_input": d["high"],
                "value_at_low": val_low,
                "value_at_high": val_high,
                "delta_low": val_low - base_value,
                "delta_high": val_high - base_value,
                "swing": abs(val_high - val_low),
            }
        )

    tornado.sort(key=lambda item: (-item["swing"], item["id"]))
    top_3 = [item["id"] for item in tornado[:3]]

    pessimistic_value = _eval_ast(tree, pess_env)
    optimistic_value = _eval_ast(tree, opt_env)

    target = float(spec.get("breakeven_target", 0.0))
    breakeven_var = str(spec.get("breakeven_var") or spec.get("threshold_var") or "").strip()

    breakevens: Dict[str, Dict[str, Any]] = {}
    equal_at_probes: set[str] = set()
    for d in drivers:
        be, all_equal = _solve_breakeven(tree, base_env, d, target)
        if be is not None:
            breakevens[d["id"]] = be
        if all_equal:
            equal_at_probes.add(d["id"])

    primary_be: Optional[Dict[str, Any]] = None
    if breakeven_var:
        if breakeven_var not in driver_map:
            raise ValueError(f"breakeven_var '{breakeven_var}' не найден в drivers")
        primary_be = breakevens.get(breakeven_var)
    else:
        for item in tornado:
            if item["id"] in breakevens:
                primary_be = breakevens[item["id"]]
                break

    if primary_be is not None:
        threshold_text = primary_be["threshold_text"]
        threshold_status = "in_range" if primary_be["in_checked_range"] else "out_of_range"
    else:
        if breakeven_var:
            target_equal_at_probes = breakeven_var in equal_at_probes
        else:
            target_equal_at_probes = (
                len(equal_at_probes) == len(drivers)
                and pessimistic_value == base_value == optimistic_value == target
            )
        if target_equal_at_probes:
            threshold_status = "equal_at_probes"
            var_sfx = f" для {breakeven_var}" if breakeven_var else ""
            threshold_text = (
                f"на расчётных пробах получено равенство цели (target={_fmt_num(target)}){var_sfx}: "
                f"переход не объявляется"
            )
        else:
            threshold_status = "not_reached"
            threshold_text = f"точка безубыточности (target={_fmt_num(target)}) не достигнута в одном драйвере"

    # Collect valid ledger IDs without duplicates, preserving appearance order
    derived_from: List[str] = []
    seen_refs: set[str] = set()
    for d in drivers:
        for ref in d["refs"]:
            if _REF_RE.match(ref) and ref not in seen_refs:
                seen_refs.add(ref)
                derived_from.append(ref)

    non_fact_drivers = [d["id"] for d in drivers if d["type"] != "fact"]
    unit = str(spec.get("unit") or "").strip()
    unit_sfx = f" {unit}" if unit else ""

    stmt = (
        f"Расчётная оценка модели ({formula}): базовый сценарий {_fmt_num(base_value)}{unit_sfx} "
        f"(диапазон {_fmt_num(pessimistic_value)}…{_fmt_num(optimistic_value)}{unit_sfx}); "
        f"{threshold_text}; топ-3 драйвера: {', '.join(top_3)}."
    )
    claim_candidate: Dict[str, Any] = {
        "id": claim_id,
        "type": "forecast",
        "statement": stmt,
        "confidence": {
            "level": "low" if non_fact_drivers else "medium",
            "rationale": (
                "Расчёт выполнен по диапазонам драйверов (read-only); "
                + (
                    f"опирается на допущения/гипотезы: {', '.join(non_fact_drivers)}."
                    if non_fact_drivers
                    else "все входные драйверы типа fact."
                )
            ),
        },
        "limitations": (
            "Производный расчётный кандидат (tools/econ.py); не является первичным фактом "
            "и не записывается автоматически в реестр."
        ),
    }
    insertable = bool(derived_from)
    insertable_reason: Optional[str] = None
    if insertable:
        claim_candidate["derived_from"] = derived_from
        derived_claim: Optional[Dict[str, Any]] = claim_candidate
    else:
        derived_claim = None
        insertable_reason = (
            "Отсутствуют ссылки на объекты реестра (derived_from пуст): "
            "type=forecast без evidence/derived_from отклоняется ledger.py validate. "
            "Добавьте ref/refs (CLM/HYP/...) к драйверам."
        )

    return {
        "name": str(spec.get("name") or ""),
        "decision": str(spec.get("decision") or ""),
        "formula": formula,
        "unit": unit,
        "scenarios": {
            "pessimistic": pessimistic_value,
            "base": base_value,
            "optimistic": optimistic_value,
        },
        "breakeven": primary_be,
        "breakevens": breakevens,
        "tornado": tornado,
        "top_3": top_3,
        "decision_threshold": threshold_text,
        "threshold_status": threshold_status,
        "insertable": insertable,
        "insertable_reason": insertable_reason,
        "derived_claim": derived_claim,
        "draft_claim": claim_candidate,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M08: Расчёт сценариев, безубыточности и tornado из YAML драйверов (read-only)."
    )
    parser.add_argument("drivers_yaml", type=pathlib.Path, help="Путь к YAML с драйверами")
    parser.add_argument("--claim-id", default="CLM-901", help="ID для черновика CLM forecast")
    parser.add_argument("--json", action="store_true", help="Вывод результата в JSON")
    parser.add_argument("--yaml", dest="as_yaml", action="store_true", help="Вывод результата в YAML")
    args = parser.parse_args(argv)

    spec = yaml.safe_load(args.drivers_yaml.read_text(encoding="utf-8"))
    result = compute_model(spec, claim_id=args.claim_id)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.as_yaml:
        print(yaml.safe_dump(result, allow_unicode=True, sort_keys=False))
    else:
        sc = result["scenarios"]
        u = f" {result['unit']}" if result["unit"] else ""
        print(f"Формула: {result['formula']}")
        print(
            f"Сценарии: pessimistic={sc['pessimistic']:.4g}{u}, "
            f"base={sc['base']:.4g}{u}, optimistic={sc['optimistic']:.4g}{u}"
        )
        print(f"Порог: {result['decision_threshold']}")
        print("Tornado топ-3: " + ", ".join(result["top_3"]))
        if result["derived_claim"] is not None:
            print("Derived CLM:", result["derived_claim"]["statement"])
        else:
            print("Draft CLM (не готов к вставке в реестр):", result["draft_claim"]["statement"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
