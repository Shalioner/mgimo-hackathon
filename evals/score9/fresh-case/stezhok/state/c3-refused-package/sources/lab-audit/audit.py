#!/usr/bin/env python3
"""Synthetic laboratory tool: records audit cards; does not verify truth."""
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);a=p.parse_args()
rows=json.loads(Path(a.input).read_text())
if not isinstance(rows,list) or not rows: p.error('nonempty list required')
for row in rows:
 if set(row)!={'promise','source','scope','missing'} or any(not isinstance(v,str) or not v.strip() for v in row.values()): p.error('four nonempty text fields required')
lines=['# Карточки аудита','СИНТЕТИЧЕСКАЯ ЛАБОРАТОРИЯ. Введённые основания не проверяются автоматически.']
for row in rows:
 lines += ['', '## '+row['promise'], 'Источник: '+row['source'], 'Граница: '+row['scope'], 'Недостающее свидетельство: '+row['missing']]
Path(a.output).write_text('\n'.join(lines)+'\n')
print(json.dumps({'written':len(rows),'output':a.output},ensure_ascii=False))
