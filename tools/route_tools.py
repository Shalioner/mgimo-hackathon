#!/usr/bin/env python3
"""Choose and run one read-only ledger inspection, with no model-generated argv."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import ledger as L
import tool_router as T

CANDIDATES = [
    {'id': 'brief', 'description': 'Показать краткий реестр объектов и оснований существующего проекта'},
    {'id': 'stats', 'description': 'Посчитать статистику объектов реестра существующего проекта'},
    {'id': 'validate', 'description': 'Проверить ошибки структуры и ссылок существующего реестра без изменений'},
]


def run(request, project, *, execute=False, load_config=L.load_routing, timeout=240):
    project = Path(project).resolve(strict=True)
    if not project.is_dir() or not (project / 'state/ledger').is_dir():
        raise ValueError('Existing project with state/ledger required')
    selection = T.select_action(request, 'Read-only inspection of an existing ledger; no external data or writes.',
                                CANDIDATES, execute=execute, load_config=load_config, timeout=timeout)
    report = {'selection': selection, 'execution': {'status': 'not_started'}}
    if selection.get('status') != 'selected':
        return report
    def handler(command):
        def invoke():
            proc = subprocess.run([sys.executable, str(L.ROOT / 'tools/ledger.py'), command, str(project)],
                                  capture_output=True, text=True, timeout=30, check=False, encoding="utf-8")
            # Validation errors are useful tool results, not failed dispatches.
            return {'exit_code': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}
        return invoke
    report['execution'] = T.execute_selected(selection, CANDIDATES,
                                            {c['id']: handler(c['id']) for c in CANDIDATES})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request-file', required=True, type=Path)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--execute', action='store_true', help='Classify and run one read-only ledger helper')
    args = parser.parse_args(argv)
    try:
        result = run(args.request_file.read_text(encoding="utf-8"), args.project, execute=args.execute)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['selection']['status'] in {'pending', 'abstained'} or result['execution']['status'] == 'succeeded' else 3


if __name__ == '__main__':
    import sys as _sys
    for _stream in (_sys.stdin, _sys.stdout, _sys.stderr):  # Windows: pipes/console default to cp1251
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
