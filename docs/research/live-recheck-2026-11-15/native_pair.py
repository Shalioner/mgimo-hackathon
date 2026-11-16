"""Explicit two-turn live smoke: same native account, read-only ephemeral threads.
Run only by explicit request; output contains selected notifications, no credentials.
"""
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools'))
import codex_routed as C
BINARY = '/Users/shalioner/.local/bin/codex'
PROMPT = 'Извлеки число из строки «Количество объектов: 17». Ответь только числом. Не используй инструменты.'


def run(mode, cwd):
    command = ([sys.executable, str(ROOT / 'tools/codex_routed.py'), '--binary', BINARY]
               if mode == 'routed' else [BINARY, 'app-server', '--stdio'])
    started = time.monotonic()
    report = {'mode': mode, 'prompt_sha256': hashlib.sha256(PROMPT.encode()).hexdigest(),
              'status': 'not_completed', 'usage_events': [], 'tool_items': [], 'routing': []}
    with tempfile.TemporaryFile() as errors:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=errors, start_new_session=True, cwd=cwd)
        sel = selectors.DefaultSelector(); sel.register(child.stdout, selectors.EVENT_READ)
        buf = b''
        def send(msg):
            child.stdin.write(C._encode(msg)); child.stdin.flush()
        try:
            send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'clientInfo':{'name':'strategist_comparison','version':'1.0'}}})
            deadline = time.monotonic() + 180
            done = False
            while not done and time.monotonic() < deadline:
                if not sel.select(timeout=1):
                    if child.poll() is not None: raise RuntimeError('server_closed')
                    continue
                chunk = os.read(child.stdout.fileno(), 65536)
                if not chunk: raise RuntimeError('server_eof')
                buf += chunk
                while b'\n' in buf:
                    raw, buf = buf.split(b'\n', 1); msg = json.loads(raw)
                    method = msg.get('method',''); params = msg.get('params',{})
                    if method and 'id' in msg:
                        send({'jsonrpc':'2.0','id':msg['id'],'error':{'code':-32601,'message':'Interactive requests disabled in smoke'}})
                    elif msg.get('error'):
                        report['error'] = msg['error']; done=True; break
                    elif msg.get('id') == 1:
                        send({'jsonrpc':'2.0','method':'initialized','params':{}})
                        send({'jsonrpc':'2.0','id':2,'method':'thread/start','params':{'ephemeral':True,'cwd':cwd,'approvalPolicy':'never','sandbox':'read-only','model':'gpt-6-luna'}})
                    elif msg.get('id') == 2:
                        report['thread_model'] = msg['result'].get('model')
                        turn={'threadId':msg['result']['thread']['id'],'input':[{'type':'text','text':PROMPT}]}
                        if mode == 'direct': turn.update(model='gpt-6-luna',effort='low')
                        send({'jsonrpc':'2.0','id':3,'method':'turn/start','params':turn})
                    elif 'token' in method.lower() and 'usage' in method.lower():
                        report['usage_events'].append({'method':method,'params':params})
                    elif method == 'item/completed':
                        item=params.get('item',{})
                        if item.get('type') == 'agentMessage': report['text']=item.get('text')
                        elif item.get('type') not in ('reasoning','userMessage'):
                            report['tool_items'].append(item.get('type'))
                    elif method == 'turn/completed':
                        report['status']=params.get('turn',{}).get('status');done=True;break
            if not done: report['status']='timeout'
        except Exception as exc:
            report.update(status='failed', exception=type(exc).__name__)
        finally:
            sel.close()
            if child.stdin: child.stdin.close()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: C._stop(child)
            child.stdout.close()
            errors.seek(0)
            diagnostic_lines = errors.read().decode('utf-8','replace').splitlines()
            for line in diagnostic_lines:
                try: item=json.loads(line)
                except ValueError: continue
                if isinstance(item,dict) and item.get('event')=='routing': report['routing'].append(item)
            report['nonrouting_stderr_lines']=len(diagnostic_lines)-len(report['routing'])
    report['elapsed_seconds']=round(time.monotonic()-started,3)
    return report


if __name__ == '__main__':
    out = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='strategist-live-pair-') as cwd:
        reports=[]
        for mode in ('routed','direct'):
            report=run(mode,cwd);reports.append(report)
            (out / (mode + '.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(report,ensure_ascii=False), flush=True)
    (out/'pair-input.json').write_text(json.dumps({'prompt':PROMPT,'same_cwd_and_settings':True,
        'repetitions':1,'order':['routed','direct'],'limits':'single simple extraction; not general quality/cost benchmark'},ensure_ascii=False,indent=2)+'\n')
