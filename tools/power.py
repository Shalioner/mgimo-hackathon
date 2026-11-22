#!/usr/bin/env python3
"""M11: Калькулятор статистической мощности, объёма выборки, MDE и ДИ Уилсона (stdlib).

Явные формулы:
1) Объём выборки на каждую группу (1:1) для сравнения двух долей p1 и p2 = p1 + mde
   (нормальное приближение с объединённой нулевой и необъединённой альтернативной дисперсией;
   эквивалент Fleiss/Kelsey и R power.prop.test без поправки на непрерывность):
       p_bar = (p1 + p2) / 2
       n_exact = ((z_{1-alpha/2}*sqrt(2*p_bar*(1-p_bar)) + z_{1-beta}*sqrt(p1*(1-p1) + p2*(1-p2))) / mde)^2
       n_per_group = ceil(n_exact)
   Для one-sided теста вместо alpha/2 используется alpha.
   Эталон для p1=0.265, mde=0.03 (p2=0.295), alpha=0.05 (two-sided), power=0.80:
       z_{0.975}=1.959964, z_{0.80}=0.841621 -> n_exact = 3515.118954 -> n_per_group = 3516.

2) Доверительный интервал Уилсона для доли p_hat = k / n:
       center = (p_hat + z^2 / (2n)) / (1 + z^2 / n)
       half   = z * sqrt(p_hat*(1 - p_hat)/n + z^2 / (4n^2)) / (1 + z^2 / n)

3) Минимально обнаружимый эффект (MDE) при заданном n на группу:
       обращение монотонной функции n_exact(p1, p1 ± mde) = n бисекцией с точностью 1e-12.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from fractions import Fraction
from typing import Dict, List


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def norm_ppf(prob: float) -> float:
    """Квантиль стандартного нормального распределения бисекцией по math.erf (точность ~1e-15)."""
    if not (0.0 < prob < 1.0):
        raise ValueError(f"Вероятность должна быть строго в (0, 1), получено {prob}")
    lo, hi = -10.0, 10.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if _norm_cdf(mid) < prob:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _exact_n_two_prop(
    p1: float,
    p2: float,
    alpha: float,
    power: float,
    alternative: str,
) -> float:
    diff = abs(p2 - p1)
    if diff <= 0.0:
        return float("inf")
    z_alpha = norm_ppf(1.0 - alpha / 2.0 if alternative == "two-sided" else 1.0 - alpha)
    z_beta = norm_ppf(power)
    p_bar = 0.5 * (p1 + p2)
    num = z_alpha * math.sqrt(2.0 * p_bar * (1.0 - p_bar)) + z_beta * math.sqrt(
        p1 * (1.0 - p1) + p2 * (1.0 - p2)
    )
    return (num / diff) ** 2


def sample_size_two_proportions(
    p: float,
    mde: float,
    alpha: float = 0.05,
    power: float = 0.80,
    alternative: str = "two-sided",
) -> Dict[str, float | int | str]:
    """Возвращает объём выборки на группу (n_per_group) и точное значение (n_exact)."""
    if not (0.0 < p < 1.0):
        raise ValueError("Базовая доля p должна быть в (0, 1)")
    if mde == 0.0 or not (0.0 < p + mde < 1.0):
        raise ValueError("p + mde должно быть в (0, 1) и mde != 0")
    if not (0.0 < alpha < 1.0) or not (0.0 < power < 1.0):
        raise ValueError("alpha и power должны быть в (0, 1)")
    if alternative not in {"two-sided", "one-sided"}:
        raise ValueError("alternative должен быть 'two-sided' или 'one-sided'")

    p2 = p + mde
    n_exact = _exact_n_two_prop(p, p2, alpha, power, alternative)
    n_per_group = int(math.ceil(n_exact - 1e-12))
    return {
        "formula": "fleiss_kelsey_unpooled_alt_normal_approx",
        "p1": float(p),
        "p2": float(p2),
        "mde": float(mde),
        "alpha": float(alpha),
        "power": float(power),
        "alternative": alternative,
        "n_exact": n_exact,
        "n_per_group": n_per_group,
        "n_total": 2 * n_per_group,
    }


def mde_two_proportions(
    p: float,
    n_per_group: int,
    alpha: float = 0.05,
    power: float = 0.80,
    alternative: str = "two-sided",
    direction: str = "up",
) -> Dict[str, float | int | str]:
    """Вычисляет минимально обнаружимый эффект MDE (в долях и п.п.) при заданном n на группу."""
    if not (0.0 < p < 1.0):
        raise ValueError("Базовая доля p должна быть в (0, 1)")
    if n_per_group <= 0:
        raise ValueError("n_per_group должен быть > 0")
    if not (0.0 < alpha < 1.0) or not (0.0 < power < 1.0):
        raise ValueError("alpha и power должны быть в (0, 1)")
    if alternative not in {"two-sided", "one-sided"}:
        raise ValueError("alternative должен быть 'two-sided' или 'one-sided'")
    if direction not in {"up", "down"}:
        raise ValueError("direction должен быть 'up' или 'down'")

    max_delta = (1.0 - p - 1e-9) if direction == "up" else (p - 1e-9)
    sign = 1.0 if direction == "up" else -1.0

    # Check if even maximal delta cannot reach target n_per_group
    if _exact_n_two_prop(p, p + sign * max_delta, alpha, power, alternative) > n_per_group:
        raise ValueError("Невозможно достичь заданную мощность при таком маленьком n_per_group")

    lo, hi = 1e-9, max_delta
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        n_req = _exact_n_two_prop(p, p + sign * mid, alpha, power, alternative)
        if n_req > n_per_group:
            lo = mid
        else:
            hi = mid
    mde_abs = 0.5 * (lo + hi)
    return {
        "formula": "fleiss_kelsey_inverse_mde",
        "p1": float(p),
        "p2": float(p + sign * mde_abs),
        "n_per_group": int(n_per_group),
        "alpha": float(alpha),
        "power": float(power),
        "alternative": alternative,
        "direction": direction,
        "mde": mde_abs,
        "mde_pp": mde_abs * 100.0,
    }


def wilson_ci(k: float, n: int, alpha: float = 0.05) -> Dict[str, float | int]:
    """Доверительный интервал Уилсона для доли успехов k из n наблюдений."""
    if n <= 0:
        raise ValueError("n должен быть > 0")
    if k < 0 or k > n:
        raise ValueError("k должен быть в [0, n]")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha должен быть в (0, 1)")

    z = norm_ppf(1.0 - alpha / 2.0)
    z2 = z * z
    p_hat = k / n
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2.0 * n)) / denom
    half = (z * math.sqrt((p_hat * (1.0 - p_hat)) / n + z2 / (4.0 * n * n))) / denom
    low = max(0.0, center - half)
    high = min(1.0, center + half)
    return {
        "k": float(k),
        "n": int(n),
        "p_hat": p_hat,
        "alpha": float(alpha),
        "confidence": 1.0 - alpha,
        "low": low,
        "high": high,
        "half_width": half,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M11: Расчёт объёма выборки n, MDE и интервала Уилсона (stdlib)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_n = sub.add_parser("n", help="Объём выборки на группу для двух долей")
    p_n.add_argument("--p", type=float, required=True, help="Базовая доля (напр. 0.265)")
    p_n.add_argument("--mde", type=float, required=True, help="Ожидаемый сдвиг в долях (напр. 0.03)")
    p_n.add_argument("--alpha", type=float, default=0.05)
    p_n.add_argument("--power", type=float, default=0.80)
    p_n.add_argument("--alternative", choices=("two-sided", "one-sided"), default="two-sided")
    p_n.add_argument("--json", action="store_true")

    p_mde = sub.add_parser("mde", help="Минимально обнаружимый эффект при заданном n")
    p_mde.add_argument("--p", type=float, required=True, help="Базовая доля")
    p_mde.add_argument("--n", type=int, required=True, help="Размер одной группы")
    p_mde.add_argument("--alpha", type=float, default=0.05)
    p_mde.add_argument("--power", type=float, default=0.80)
    p_mde.add_argument("--alternative", choices=("two-sided", "one-sided"), default="two-sided")
    p_mde.add_argument("--direction", choices=("up", "down"), default="up")
    p_mde.add_argument("--json", action="store_true")

    p_w = sub.add_parser("wilson", help="Доверительный интервал Уилсона")
    p_w.add_argument("--k", type=float, required=True, help="Число успехов")
    p_w.add_argument("--n", type=int, required=True, help="Всего наблюдений")
    p_w.add_argument("--alpha", type=float, default=0.05)
    p_w.add_argument("--json", action="store_true")

    p_missing = sub.add_parser("missing", help="Доля наблюдений с неизвестным исходом")
    p_missing.add_argument("--eligible", type=int, required=True, help="Пригодные наблюдения")
    p_missing.add_argument("--known", type=int, required=True, help="Пригодные наблюдения с известным исходом")
    p_missing.add_argument("--limit-percent", type=float, required=True, help="Допустимая доля пропусков в процентах")
    p_missing.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "missing":
        if not 0 <= args.known <= args.eligible:
            p_missing.error("нужно 0 ≤ известные исходы ≤ пригодные наблюдения")
        if not 0 <= args.limit_percent <= 100:
            p_missing.error("порог должен быть в [0, 100] процентах")

        unknown = args.eligible - args.known
        percent = Fraction(100 * unknown, args.eligible) if args.eligible else None
        threshold = Fraction(str(args.limit_percent))
        status = "unknown" if percent is None else "pass" if percent <= threshold else "hold"
        result = {
            "eligible": args.eligible,
            "known": args.known,
            "unknown": unknown,
            "missing_percent": float(percent) if percent is not None else None,
            "limit_percent": args.limit_percent,
            "missingness_gate": status,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif percent is None:
            print("Доля пропусков неизвестна: нет пригодных наблюдений.")
        else:
            outcome = "соблюдён" if status == "pass" else "превышен"
            print(f"Пропуски: {unknown}/{args.eligible} = {float(percent):.6g}%. "
                  f"Порог пропусков {args.limit_percent:g}% {outcome}.")
        return 0

    if args.cmd == "n":
        res = sample_size_two_proportions(
            args.p, args.mde, alpha=args.alpha, power=args.power, alternative=args.alternative
        )
    elif args.cmd == "mde":
        res = mde_two_proportions(
            args.p,
            args.n,
            alpha=args.alpha,
            power=args.power,
            alternative=args.alternative,
            direction=args.direction,
        )
    else:
        res = wilson_ci(args.k, args.n, alpha=args.alpha)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        if args.cmd == "n":
            print(
                f"n на группу = {res['n_per_group']} (точное {res['n_exact']:.2f}, всего {res['n_total']}); "
                f"p1={res['p1']}, p2={res['p2']}, alpha={res['alpha']}, power={res['power']}"
            )
        elif args.cmd == "mde":
            print(
                f"MDE = {res['mde']:.6f} ({res['mde_pp']:.2f} п.п.) при n={res['n_per_group']} на группу"
            )
        else:
            print(
                f"p_hat={res['p_hat']:.4f}, Wilson {int(res['confidence']*100)}% CI: "
                f"[{res['low']:.4f}, {res['high']:.4f}]"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
