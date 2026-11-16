import copy
import json
import os
from pathlib import Path
import subprocess
import signal
import sys

import pytest

import codex_routed as R


def turn(text='Extract facts', identifier=3):
    return {'jsonrpc': '2.0', 'id': identifier, 'method': 'turn/start',
            'params': {'threadId': 'thread-1', 'input': [{'type': 'text', 'text': text}],
                       'model': 'expensive-old-model', 'effort': 'high',
                       'approvalPolicy': 'on-request', 'sandboxPolicy': {'type': 'readOnly'}}}


def decide(payload, *, execute):
    assert payload['host'] == 'codex' and execute
    return {'status': 'selected', 'host': 'codex', 'model': 'gpt-6-luna', 'effort': 'low', 'tier': 'T1'}


def authorize(selection):
    assert selection['status'] == 'selected'
    return {'status': 'authorized'}


def test_route_preserves_permissions_and_input_and_resets_previous_effort():
    original = turn()
    before = copy.deepcopy(original)
    routed, error, receipt = R.route_turn(original, decide=decide, authorize=authorize)
    assert error is None and original == before
    expected = copy.deepcopy(original)
    expected['params'].update(model='gpt-6-luna', effort='low')
    assert routed == expected
    assert receipt['observed_model'] is None and 'Extract facts' not in json.dumps(receipt)


def test_collaboration_mode_priority_and_instructions_preserved():
    original = turn()
    original['params']['collaborationMode'] = {'mode': 'default', 'settings': {
        'model': 'old', 'reasoning_effort': 'high', 'developer_instructions': 'Keep my project rules'}}
    routed, error, _ = R.route_turn(original, decide=decide, authorize=authorize)
    assert error is None
    assert routed['params']['collaborationMode']['settings'] == {
        'model': 'gpt-6-luna', 'reasoning_effort': 'low', 'developer_instructions': 'Keep my project rules'}


@pytest.mark.parametrize('status', ['unavailable', 'needs_review', 'disabled'])
def test_selection_failure_returns_local_error(status):
    routed, error, _ = R.route_turn(turn(), decide=lambda *a, **kw: {'status': status},
                                   authorize=lambda *a: pytest.fail('No authorize'))
    assert routed is None and error['id'] == 3 and error['error']['code'] == -32001


def test_authorization_failure_blocks_instead_of_falling_back():
    routed, error, _ = R.route_turn(turn(), decide=decide, authorize=lambda *a: {'status': 'denied'})
    assert routed is None and error


@pytest.mark.parametrize('bad', ['image', 'empty', 'large', 'bad_mode', 'bad_id'])
def test_ineligible_input_never_classified(bad):
    data = turn()
    if bad == 'image':
        data['params']['input'].append({'type': 'image', 'url': 'private'})
    elif bad == 'empty':
        data['params']['input'] = []
    elif bad == 'large':
        data['params']['input'][0]['text'] = 'x' * 24001
    elif bad == 'bad_id':
        data['id'] = True
    else:
        data['params']['collaborationMode'] = {'settings': None}
    routed, error, _ = R.route_turn(data, decide=lambda *a, **kw: pytest.fail('No classifier'))
    assert routed is None and error


def test_nonturn_protocol_unchanged():
    for message in [{'method': 'initialize', 'id': 1}, {'method': 'initialized'},
                    {'id': 5, 'result': {'decision': 'decline'}}, {'method': 'turn/interrupt', 'id': 7}]:
        routed, error, receipt = R.route_turn(message, decide=lambda *a, **kw: pytest.fail('No classifier'))
        assert routed is message and error is None and receipt is None


def test_exception_does_not_expose_details():
    def fail(*a, **kw):
        raise RuntimeError('secret prompt')
    routed, error, receipt = R.route_turn(turn(), decide=fail)
    assert routed is None and 'secret prompt' not in json.dumps([error, receipt])


def test_fake_child_wire_e2e(tmp_path):
    child = tmp_path / 'fake_server.py'
    child.write_text('''import sys,json,os
for line in sys.stdin:
    data=json.loads(line)
    if data.get('method')=='initialize':
        print(json.dumps({'id':data['id'],'result':{'ready':True,'routed':os.environ.get('STRATEGIST_ROUTED_APP_SERVER')}}),flush=True)
        print(json.dumps({'method':'server/notification','params':{'unchanged':True}}),flush=True)
    elif data.get('method')=='turn/start':
        print(json.dumps({'id':data['id'],'result':data['params']}),flush=True)
        print(json.dumps({'id':999,'method':'item/commandExecution/requestApproval','params':{'x':1}}),flush=True)
    else:
        print(json.dumps({'method':'echo','params':data}),flush=True)
''', encoding="utf-8")
    runner = tmp_path / 'runner.py'
    runner.write_text('''import sys
sys.path.insert(0, sys.argv[1])
import codex_routed as R
def decide(payload,execute=False):
    if payload['request']=='uncertain': return {'status':'needs_review'}
    return {'status':'selected','host':'codex','model':'gpt-6-luna','effort':'low','tier':'T1'}
def authorize(selection): return {'status':'authorized'}
raise SystemExit(R.proxy([sys.executable,sys.argv[2]],decide=decide,authorize=authorize))
''', encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(runner), str(Path(R.__file__).parent), str(child)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    def send(data):
        process.stdin.write(json.dumps(data)+'\n')
        process.stdin.flush()
    def read():
        return json.loads(process.stdout.readline())
    try:
        send({'id':1,'method':'initialize','params':{}})
        assert read()['result'] == {'ready':True,'routed':'1'}
        assert read()['method'] == 'server/notification'
        send(turn())
        result = read()
        assert result['result']['model'] == 'gpt-6-luna' and result['result']['effort'] == 'low'
        assert read()['method'] == 'item/commandExecution/requestApproval'
        send({'id':999,'result':{'decision':'decline'}})
        assert read()['params'] == {'id':999,'result':{'decision':'decline'}}
        send(turn(identifier=3))
        assert read()['error']['message'].startswith('Duplicate')
        send(turn('uncertain',identifier=4))
        assert read()['error']['code'] == -32001
        send({'id':5,'method':'turn/interrupt','params':{'threadId':'t'}})
        assert read()['params']['method'] == 'turn/interrupt'
    finally:
        process.stdin.close()
        process.wait(timeout=5)
    assert process.returncode == 0
    stderr = process.stderr.read()
    assert 'Extract facts' not in stderr and 'uncertain' not in stderr
    assert '"status":"forwarded"' in stderr and '"status":"not_forwarded"' in stderr


def test_cli_invalid_binary_refused():
    result = subprocess.run([sys.executable, R.__file__, '--binary', 'codex'], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 2 and result.stdout == ''


def test_sigterm_during_classification_reaps_independent_appserver(tmp_path):
    server = tmp_path / 'sleeping_server.py'
    server.write_text('import os,json,time\nprint(json.dumps({"method":"ready","pid":os.getpid()}),flush=True)\ntime.sleep(60)\n', encoding="utf-8")
    runner = tmp_path / 'blocked_proxy.py'
    runner.write_text('''import sys,time
sys.path.insert(0,sys.argv[1])
import codex_routed as R
def decide(*a,**kw):
    print('CLASSIFYING',file=sys.stderr,flush=True)
    time.sleep(60)
raise SystemExit(R.proxy([sys.executable,sys.argv[2]],decide=decide))
''', encoding="utf-8")
    process = subprocess.Popen([sys.executable,str(runner),str(Path(R.__file__).parent),str(server)],
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               text=True,start_new_session=True, encoding="utf-8")
    server_pid = None
    try:
        server_pid = json.loads(process.stdout.readline())['pid']
        process.stdin.write(json.dumps(turn())+'\n')
        process.stdin.flush()
        assert process.stderr.readline().strip() == 'CLASSIFYING'
        os.killpg(process.pid,signal.SIGTERM)
        process.wait(timeout=8)
        assert process.returncode == 1
        with pytest.raises(ProcessLookupError):
            os.kill(server_pid,0)
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGKILL)
            process.wait(timeout=3)
        if server_pid:
            try:
                os.kill(server_pid,signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_one_shot_client_denies_server_request_and_keeps_native_read_only(tmp_path, monkeypatch):
    fake_proxy = tmp_path / 'fake_proxy.py'
    fake_proxy.write_text('''import sys,json
for line in sys.stdin:
    m=json.loads(line)
    if m.get('method')=='initialize': print(json.dumps({'id':1,'result':{}}),flush=True)
    elif m.get('method')=='thread/start':
        assert m['params']['approvalPolicy']=='never' and m['params']['sandbox']=='read-only'
        assert m['params']['ephemeral'] is True
        print(json.dumps({'id':2,'result':{'thread':{'id':'t'}}}),flush=True)
    elif m.get('method')=='turn/start':
        assert m['params']['threadId']=='t'
        print(json.dumps({'id':77,'method':'item/commandExecution/requestApproval','params':{}}),flush=True)
    elif m.get('id')==77:
        assert 'error' in m and 'result' not in m
        print(json.dumps({'method':'item/completed','params':{'item':{'type':'agentMessage','text':'done'}}}),flush=True)
        print(json.dumps({'method':'turn/completed','params':{'turn':{'id':'turn1','status':'completed'}}}),flush=True)
''', encoding="utf-8")
    monkeypatch.setattr(R, '__file__', str(fake_proxy))
    result = R.prompt_once('/not/invoked', 'Hello', timeout=5)
    assert result == {'status':'completed','text':'done','turn_id':'turn1'}
