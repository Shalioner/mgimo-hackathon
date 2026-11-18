import copy
import json
import os
from pathlib import Path
import sys
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import native_codex as N


@pytest.fixture
def cfg():
    data = N.L.load_routing()
    data['automatic_routing'] = {'host_routing': {'enabled_hosts': ['codex'], 'models': {
        'codex': [N.L.model_for(data, 'openai', tier) for tier in ('T1', 'T2', 'T3')]}}}
    return data


@pytest.fixture
def server(tmp_path):
    def make(mode='ok'):
        path = tmp_path / 'fake-codex'
        log = tmp_path / 'messages.jsonl'
        path.write_text(f'''#!{sys.executable}
import json,sys,time,os
MODE={mode!r}
LOG={str(log)!r}
def emit(obj):
 print(json.dumps(obj),flush=True)
for line in sys.stdin:
 m=json.loads(line)
 with open(LOG,'a') as f: f.write(json.dumps(m)+'\\n')
 method=m.get('method')
 if method=='initialize': emit({{'id':1,'result':{{}}}})
 elif method=='config/read': emit({{'id':4,'result':{{'config':{{'mcp_servers':{{'example':{{'secret':'do-not-log'}}}}}}}}}})
 elif method=='thread/start':
  assert os.path.realpath(m['params']['cwd'])==os.getcwd()
  assert m['params']['ephemeral'] and m['params']['sandbox']=='read-only'
  assert m['params']['config']['mcp_servers']=={{'example':{{'enabled':False}}}}
  emit({{'id':2,'result':{{'thread':{{'id':'thread'}},'model':'not-observed'}}}})
 elif method=='turn/start':
  if MODE=='timeout': time.sleep(10)
  elif MODE=='oversize': print('x'*(4*1024*1024+1),flush=True)
  elif MODE=='request': emit({{'id':99,'method':'item/commandExecution/requestApproval','params':{{}}}})
  elif MODE=='tool': emit({{'method':'item/started','params':{{'threadId':'thread','item':{{'type':'commandExecution'}}}}}})
  elif MODE=='null_params': emit({{'method':'turn/completed','params':None}})
  elif MODE=='null_item': emit({{'method':'item/completed','params':{{'threadId':'thread','item':None}}}})
  elif MODE=='null_turn': emit({{'method':'turn/completed','params':{{'threadId':'thread','turn':None}}}})
  elif MODE=='malformed': print('[]',flush=True)
  elif MODE=='empty': emit({{'method':'turn/completed','params':{{'threadId':'thread','turn':{{'id':'turn','status':'completed'}}}}}})
  elif MODE=='error': emit({{'id':3,'error':{{'message':'secret error'}}}})
  elif MODE=='eof': sys.exit(0)
  else:
   emit({{'id':3,'result':{{'turn':{{'id':'turn'}}}}}})
   emit({{'method':'item/completed','params':{{'threadId':'thread','item':{{'type':'agentMessage','text':'answer'}}}}}})
   emit({{'method':'turn/completed','params':{{'threadId':'thread','turn':{{'id':'turn','status':'completed'}}}}}})
   time.sleep(.04)
   emit({{'method':'thread/tokenUsage/updated','params':{{'threadId':'thread','turnId':'turn','tokenUsage':{{'last':{{'totalTokens':17}}}}}}}})
''')
        path.chmod(0o700)
        return path, log
    return make


def test_plan_is_pure_and_refuses_unapproved(cfg, monkeypatch):
    before = copy.deepcopy(cfg)
    monkeypatch.setattr(N.H, 'decide', lambda *a, **k: pytest.fail('No classifier'))
    for tier in ('T1', 'T2', 'T3'):
        assert N.plan(tier, cfg)['status'] == 'selected'
    assert N.plan('T4', cfg)['status'] == 'manual'
    assert N.plan('T1', cfg, effort='max')['status'] == 'manual'
    assert cfg == before
    cfg['automatic_routing']['host_routing']['enabled_hosts'] = []
    assert N.plan('T1', cfg)['status'] == 'manual'


def test_success_usage_after_completion_and_native_params(cfg, server):
    binary, log = server()
    selected = N.plan('T1', cfg)
    receipt, text = N.execute(binary, 'text', selected, load_config=lambda: cfg, timeout=3)
    assert receipt['status'] == 'succeeded' and text == 'answer'
    assert receipt['usage_events'][0]['params']['tokenUsage']['last']['totalTokens'] == 17
    assert receipt['observed_model'] is None
    assert receipt['transmitted_model'] == selected['model']
    msgs = [json.loads(x) for x in log.read_text().splitlines()]
    assert [m.get('method') for m in msgs] == ['initialize','initialized','config/read','thread/start','turn/start']
    assert msgs[-1]['params']['effort'] == selected['effort']
    assert not Path(msgs[-2]['params']['cwd']).exists()
    assert 'do-not-log' not in json.dumps(receipt) and 'do-not-log' not in log.read_text()


def test_fresh_revocation_prevents_turn(cfg, server):
    binary, log = server()
    selected = N.plan('T1', cfg)
    cfg['automatic_routing']['host_routing']['enabled_hosts'] = []
    receipt, text = N.execute(binary, 'text', selected, load_config=lambda: cfg, timeout=3)
    assert receipt['status'] == 'manual' and text is None, receipt
    assert receipt['transmitted_model'] is None
    assert 'turn/start' not in log.read_text()


@pytest.mark.parametrize('mode', ['request', 'tool', 'error', 'eof', 'oversize', 'timeout', 'malformed', 'null_params', 'null_item', 'null_turn', 'empty'])
def test_fail_closed_without_retry(cfg, server, mode):
    binary, log = server(mode)
    receipt, text = N.execute(binary, 'text', N.plan('T1', cfg), load_config=lambda: cfg,
                              timeout=2 if mode == 'timeout' else 3)
    assert receipt['status'] == 'failed' and text is None
    assert log.read_text().count('turn/start') == 1
    assert 'secret error' not in json.dumps(receipt)


def test_t4_never_starts_child(cfg, monkeypatch):
    monkeypatch.setattr(N.subprocess, 'Popen', lambda *a, **k: pytest.fail('No child'))
    receipt, text = N.execute('/bin/false', 'text', {'status':'selected','host':'codex','tier':'T4'}, load_config=lambda: cfg)
    assert receipt['status'] == 'manual' and text is None, receipt


def test_bad_config_fails_closed():
    with pytest.raises(ValueError):
        N.text_only_config(None)


def test_no_tools_configuration_projects_names_only():
    config = N.text_only_config({'mcp_servers': {'a.b': {'token': 'secret'}}})
    assert config['mcp_servers'] == {'a.b': {'enabled': False}}
    assert config['default_permissions'] == ':read-only'
    assert config['web_search'] == 'disabled'
    assert all(config['features.' + feature] is False for feature in N.DISABLED_FEATURES)
    assert 'secret' not in json.dumps(config)


@pytest.mark.parametrize('timeout', [0, -1, True, float('nan'), float('inf')])
def test_invalid_deadline_does_not_spawn(cfg, monkeypatch, timeout):
    monkeypatch.setattr(N.subprocess, 'Popen', lambda *a, **k: pytest.fail('No child'))
    receipt, text = N.execute('/bin/false', 'text', N.plan('T1', cfg), timeout=timeout)
    assert receipt['status'] == 'manual' and text is None


def test_worker_thread_does_not_install_signal_handlers(cfg, server, monkeypatch):
    binary, _ = server()
    monkeypatch.setattr(N.signal, 'signal', lambda *a: pytest.fail('Global handler in worker'))
    with ThreadPoolExecutor(max_workers=1) as pool:
        receipt, text = pool.submit(N.execute, binary, 'text', N.plan('T1', cfg),
                                    load_config=lambda: cfg, timeout=3).result(timeout=5)
    assert receipt['status'] == 'succeeded' and text == 'answer'


def test_sigterm_cleans_child_process_group_and_restores_handler(cfg, tmp_path):
    binary = tmp_path / 'sleeping-server'
    pid_file = tmp_path / 'server.pid'
    binary.write_text(f'#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(pid_file)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n')
    binary.chmod(0o700)
    runner = tmp_path / 'runner.py'
    runner.write_text("import sys,json,signal\n"
                      + f"sys.path.insert(0, {str(Path(N.__file__).parent)!r})\n"
                      + "import native_codex as N\n"
                      + f"cfg=json.loads({json.dumps(cfg)!r})\n"
                      + "previous=signal.getsignal(signal.SIGTERM)\n"
                      + f"receipt,text=N.execute({str(binary)!r}, 'text', N.plan('T1', cfg), load_config=lambda:cfg, timeout=20)\n"
                      + "assert signal.getsignal(signal.SIGTERM)==previous\n"
                      + "print(json.dumps(receipt),flush=True)\n")
    process = subprocess.Popen([sys.executable, str(runner)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    child_pid = None
    try:
        end = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < end:
            time.sleep(.02)
        assert pid_file.exists()
        child_pid = int(pid_file.read_text())
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        receipt = json.loads(stdout)
        assert receipt['status'] == 'failed'
        assert receipt['reason'] == 'Native execution cancelled'
        with pytest.raises(ProcessLookupError):
            os.killpg(child_pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child_pid is not None:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
