"""Bounded T0 arithmetic verification against caller-supplied source operands.

The caller, not the answer model, supplies this optional JSON context contract::

    {"calculation_sources": {"SRC-A": {"locator": "table 1, rows 1-2",
        "values": {"a": 95, "b": 20, "weight": 0.5}}},
     "calculation_checks": [{"id": "standardized", "answer_pointer": "/facts/rate",
        "expression": {"op": "weighted_mean", "values": [
            {"source": "SRC-A", "pointer": "/values/a"},
            {"source": "SRC-A", "pointer": "/values/b"}], "weights": [
            {"source": "SRC-A", "pointer": "/values/weight"},
            {"source": "SRC-A", "pointer": "/values/weight"}]},
        "tolerance": "0.01"}]}

Other operations use ``args``: sum/multiply (1..32), subtract/ratio/percent (2).
Percent means 100 * numerator / denominator. Expressions may nest up to depth 8.
All numeric leaves reference source values; no literals, code, eval or answer refs.
Provenance is returned, not independently authenticated: a caller must establish
that source values and locators faithfully represent its materials. Verification
covers only declared numeric fields, never the rest of the answer's semantics.
"""
from __future__ import annotations

from decimal import Decimal, DecimalException, localcontext
import json
import re

MAX_BYTES = 256_000
MAX_CHECKS = 64
MAX_SOURCES = 100
MAX_ARGUMENTS = 32
MAX_DEPTH = 8
MAX_MAGNITUDE = Decimal('1e18')
MIN_MAGNITUDE = Decimal('1e-18')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z')


def _json(value):
    if isinstance(value, str):
        if len(value.encode('utf-8')) > MAX_BYTES:
            raise ValueError('JSON exceeds byte limit')
        return json.loads(value, parse_float=Decimal,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError('Numeric JSON value required')
    number = Decimal(str(value))
    if (not number.is_finite() or number.copy_abs() > MAX_MAGNITUDE
            or (number and number.copy_abs() < MIN_MAGNITUDE) or len(number.as_tuple().digits) > 50):
        raise ValueError('Numeric value must be finite, within 1e-18..1e18 (or zero), and at most 50 digits')
    return number


def _pointer(obj, pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/') or len(pointer) > 512:
        raise ValueError('Bounded absolute JSON pointer required')
    parts = pointer[1:].split('/')
    if len(parts) > 16:
        raise ValueError('JSON pointer is too deep')
    for part in parts:
        if re.search(r'~(?![01])', part):
            raise ValueError('Invalid JSON pointer escape')
        key = part.replace('~1', '/').replace('~0', '~')
        if isinstance(obj, dict):
            if key not in obj:
                raise ValueError('JSON pointer target missing')
            obj = obj[key]
        elif isinstance(obj, list) and re.fullmatch(r'0|[1-9][0-9]*', key):
            index = int(key)
            if index >= len(obj):
                raise ValueError('JSON pointer array index missing')
            obj = obj[index]
        else:
            raise ValueError('JSON pointer target missing')
    return obj


def _expression(expr, sources, references, depth=0, budget=None):
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > MAX_DEPTH or budget[0] > 256 or not isinstance(expr, dict):
        raise ValueError('Invalid or oversized calculation expression')
    if set(expr) == {'source', 'pointer'}:
        source_id, pointer = expr['source'], expr['pointer']
        if not isinstance(source_id, str) or source_id not in sources:
            raise ValueError('Unknown calculation source')
        if not isinstance(pointer, str) or not pointer.startswith('/values/'):
            raise ValueError('Operand must reference source values')
        source = sources[source_id]
        value = _number(_pointer(source, pointer))
        references[(source_id, pointer)] = {'source': source_id, 'pointer': pointer,
                                           'locator': source['locator'], 'value': str(value)}
        return value
    op = expr.get('op')
    def evaluate(items):
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ARGUMENTS:
            raise ValueError('Invalid calculation argument count')
        return [_expression(x, sources, references, depth + 1, budget) for x in items]
    if op == 'weighted_mean' and set(expr) == {'op', 'values', 'weights'}:
        values, weights = evaluate(expr['values']), evaluate(expr['weights'])
        if len(values) != len(weights) or any(w < 0 for w in weights) or sum(weights) != 1:
            raise ValueError('Weights must be nonnegative, match values and sum to one')
        result = sum(v * w for v, w in zip(values, weights))
    elif op in ('sum', 'multiply', 'subtract', 'ratio', 'percent') and set(expr) == {'op', 'args'}:
        values = evaluate(expr['args'])
        if op in ('subtract', 'ratio', 'percent') and len(values) != 2:
            raise ValueError('Binary calculation requires two arguments')
        if op == 'sum':
            result = sum(values)
        elif op == 'multiply':
            result = Decimal(1)
            for value in values:
                result *= value
                _number(result)
        elif op == 'subtract':
            result = values[0] - values[1]
        else:
            if values[1] == 0:
                raise ValueError('Division by zero')
            result = values[0] / values[1] * (100 if op == 'percent' else 1)
    else:
        raise ValueError('Unknown calculation operation or expression fields')
    return _number(result)


def check(context, answer):
    """Verify declared arithmetic; absent contract is never reported verified.

    invalid: unusable contract/source data; needs_review: invalid/missing answer
    field; failed: numeric mismatch; verified: all declared numeric fields match.
    Results are JSON serializable, with decimals represented as exact strings.
    """
    try:
        ctx = _json(context)
    except (ValueError, TypeError, RecursionError, DecimalException):
        # Ordinary prose context is supported; a malformed opted-in contract is not.
        return {'status': 'invalid' if isinstance(context, str) and 'calculation_checks' in context
                else 'not_applicable', 'checks': [], 'reason': 'Context is not valid calculation JSON'}
    if not isinstance(ctx, dict) or 'calculation_checks' not in ctx:
        return {'status': 'not_applicable', 'checks': []}
    results = []
    try:
        declarations, sources = ctx['calculation_checks'], ctx.get('calculation_sources')
        if not isinstance(declarations, list) or not 1 <= len(declarations) <= MAX_CHECKS:
            raise ValueError('Expected 1..64 calculation checks')
        if not isinstance(sources, dict) or not 1 <= len(sources) <= MAX_SOURCES:
            raise ValueError('Expected 1..100 calculation sources')
        for ident, source in sources.items():
            if (not isinstance(ident, str) or not ID.fullmatch(ident) or not isinstance(source, dict)
                    or not isinstance(source.get('locator'), str) or not source['locator'].strip()
                    or len(source['locator']) > 512 or not isinstance(source.get('values'), (dict, list))):
                raise ValueError('Source requires ID, bounded locator and values')
        ids, targets, prepared = set(), set(), []
        with localcontext() as decimal_context:
            decimal_context.prec = 50
            for declaration in declarations:
                if (not isinstance(declaration, dict) or
                        not {'id', 'answer_pointer', 'expression'} <= set(declaration) or
                        set(declaration) - {'id', 'answer_pointer', 'expression', 'tolerance'}):
                    raise ValueError('Invalid calculation check fields')
                ident, target = declaration['id'], declaration['answer_pointer']
                if not isinstance(ident, str) or not ID.fullmatch(ident) or ident in ids:
                    raise ValueError('Calculation IDs must be unique')
                if not isinstance(target, str) or not target.startswith('/facts/') or target in targets:
                    raise ValueError('Unique answer pointer within facts required')
                # Validate pointer grammar even when the answer will be unavailable.
                if len(target) > 512 or len(target[1:].split('/')) > 16 or re.search(r'~(?![01])', target):
                    raise ValueError('Invalid answer pointer')
                ids.add(ident)
                targets.add(target)
                tolerance_raw = declaration.get('tolerance', '0')
                if isinstance(tolerance_raw, bool) or not isinstance(tolerance_raw, (str, int, float, Decimal)):
                    raise ValueError('Invalid tolerance')
                tolerance = Decimal(str(tolerance_raw))
                if not tolerance.is_finite() or not 0 <= tolerance <= Decimal('0.01'):
                    raise ValueError('Tolerance must be between zero and 0.01')
                references = {}
                expected = _expression(declaration['expression'], sources, references)
                prepared.append((ident, target, expected, tolerance, list(references.values())))
        # Validate every declaration before comparing any answer. A malformed later
        # declaration must not leave a misleading partial verified result.
        try:
            response = _json(answer)
        except (ValueError, TypeError, RecursionError, DecimalException):
            response = None
        if not isinstance(response, dict):
            return {'status': 'needs_review', 'checks': [], 'reason': 'Answer must be a JSON object'}
        statuses = []
        for ident, target, expected, tolerance, references in prepared:
            result = {'id': ident, 'answer_pointer': target, 'expected': str(expected),
                      'tolerance': str(tolerance), 'sources': references, 'actual': None}
            try:
                actual = _number(_pointer(response, target))
                with localcontext() as comparison_context:
                    comparison_context.prec = 100
                    matches = (actual - expected).copy_abs() <= tolerance
                result.update(actual=str(actual), status='verified' if matches else 'failed')
            except (ValueError, TypeError, DecimalException):
                result.update(status='needs_review', reason='Missing or nonnumeric answer field')
            results.append(result)
            statuses.append(result['status'])
        status = 'failed' if 'failed' in statuses else 'needs_review' if 'needs_review' in statuses else 'verified'
        return {'status': status, 'checks': results, 'scope': 'declared_numeric_fields_only'}
    except (ValueError, TypeError, RecursionError, DecimalException) as exc:
        return {'status': 'invalid', 'checks': [], 'reason': str(exc)}
