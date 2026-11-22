# -*- coding: utf-8 -*-
"""Local check of the proposed analyst procedure, not an EXP result."""
from pathlib import Path
import datetime
import json
import subprocess

p=Path('projects/stezhok')
d=p/'state/refusal-recovery-recheck'
d.mkdir(exist_ok=True)
source=p/'sources/lab-audit/invalid.json'
original=source.read_bytes()
reference=json.loads((p/'sources/lab-audit/example.json').read_text())
rows=json.loads(original)
receipts=[]

def run(label,inp,out):
    assert not out.exists(), 'Fresh output path required for this check'
    cmd=['.venv/bin/python',str(p/'sources/lab-audit/audit.py'),'--input',str(inp),'--output',str(out)]
    r=subprocess.run(cmd,text=True,capture_output=True)
    receipt=dict(label=label,command=cmd,exit=r.returncode,stdout=r.stdout,stderr=r.stderr,output_exists=out.exists())
    receipts.append(receipt)
    return receipt

bad=run('original-incomplete',source,d/'incomplete-output.md')
assert bad['exit']==2 and not bad['output_exists']
changes=[]
for n,row in enumerate(rows):
    for key in ['promise','source','scope','missing']:
        if key not in row or isinstance(row[key],str) and not row[key].strip():
            assert key in reference[n]
            row[key]=reference[n][key]
            changes.append(dict(row=n,field=key))
corrected=d/'filled-only-missing.json'
corrected.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
good=run('corrected-copy-only-missing',corrected,d/'corrected-output.md')
assert good['exit']==0 and good['output_exists']
assert (d/'corrected-output.md').read_bytes()==(p/'sources/lab-audit/example-result.md').read_bytes()
assert source.read_bytes()==original

# Neighbour case: failed call must not turn an existing old output into success.
stale=d/'old-output.md'
stale.write_bytes((d/'corrected-output.md').read_bytes())
before=stale.read_bytes()
cmd=['.venv/bin/python',str(p/'sources/lab-audit/audit.py'),'--input',str(source),'--output',str(stale)]
r=subprocess.run(cmd,text=True,capture_output=True)
receipts.append(dict(label='failed-call-with-old-output',command=cmd,exit=r.returncode,stdout=r.stdout,stderr=r.stderr,output_exists=stale.exists(),old_bytes_unchanged=stale.read_bytes()==before))
assert r.returncode==2 and stale.read_bytes()==before
result=dict(time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),scope='Technical check of the proposed recovery instruction; not EXP-001/002, no usefulness result or time-budget feasibility claim',changed_fields=changes,commands=receipts)
(d/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False,indent=2))
