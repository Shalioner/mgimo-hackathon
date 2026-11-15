#!/usr/bin/env python3
"""Native Codex stdio app-server router. No login, account switch or API gateway.

Start with --binary /absolute/path/to/codex and connect a JSONL app-server client.
Only turn/start model/effort are rewritten; all other protocol messages pass through.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time

MAX_FRAME = 4 * 1024 * 1024


def route_turn(message, *, decide=None, authorize=None):
    """Return (forwarded request or None, local error or None, redacted receipt)."""
    if not isinstance(message, dict) or message.get('method') != 'turn/start':
        return message, None, None
    request_id = message.get('id')
    def fail(reason):
        return None, {'jsonrpc': '2.0', 'id': request_id,
                      'error': {'code': -32001, 'message': 'Automatic routing refused this turn: ' + reason}}, {
                          'event': 'routing', 'status': 'not_forwarded', 'reason': reason}
    if type(request_id) not in (int, str):
        return fail('A unique string or integer request ID is required')
    params = message.get('params')
    if not isinstance(params, dict) or not isinstance(params.get('input'), list):
        return fail('Invalid turn input')
    chunks = []
    for item in params['input']:
        if not isinstance(item, dict) or item.get('type') != 'text' or not isinstance(item.get('text'), str):
            return fail('Only explicit text input is currently supported for automatic classification')
        chunks.append(item['text'])
    request = '\n'.join(chunks)
    if not request.strip() or len(request.encode('utf-8')) > 24000:
        return fail('Nonempty routing text of at most 24000 bytes required; input is not truncated')
    mode = params.get('collaborationMode')
    if mode is not None and (not isinstance(mode, dict) or not isinstance(mode.get('settings'), dict)):
        return fail('Invalid collaboration mode settings')
    try:
        if decide is None:
            from host_routing import decide
        selection = decide({'host': 'codex', 'event': 'turn.start', 'request': request, 'context': ''}, execute=True)
    except Exception:
        return fail('Classifier unavailable')
    if not isinstance(selection, dict) or selection.get('status') != 'selected':
        return fail('No authorized confident model selection')
    model, effort = selection.get('model'), selection.get('effort')
    if not isinstance(model, str) or not model.strip() or len(model) > 200 or effort not in {'low', 'medium', 'high', 'xhigh'}:
        return fail('Invalid model or effort selection')
    try:
        if authorize is None:
            from host_routing import authorize
        authorization = authorize(selection)
    except Exception:
        return fail('Route authorization unavailable')
    if not isinstance(authorization, dict) or authorization.get('status') != 'authorized':
        return fail('Route authorization expired or quota exhausted')
    routed = copy.deepcopy(message)
    routed['params'].update(model=model, effort=effort)
    if mode is not None:
        routed['params']['collaborationMode']['settings'].update(model=model, reasoning_effort=effort)
    receipt = {'event': 'routing', 'status': 'forwarded', 'model': model, 'effort': effort,
               'tier': selection.get('tier'), 'observed_model': None}
    return routed, None, receipt


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')


def _stop(child):
    if child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=3)


def proxy(command, *, input_stream=None, output_stream=None, error_stream=None,
          decide=None, authorize=None):
    """JSONL byte relay. Existing auth/env remain native and are never inspected."""
    incoming = input_stream or sys.stdin.buffer
    outgoing = output_stream or sys.stdout.buffer
    errors = error_stream or sys.stderr.buffer
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=None, env={**os.environ, 'STRATEGIST_ROUTED_APP_SERVER': '1'},
                             start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(incoming, selectors.EVENT_READ, 'client')
    selector.register(child.stdout, selectors.EVENT_READ, 'server')
    buffers = {'client': b'', 'server': b''}
    seen_turn_ids = set()
    previous_sigterm = None
    if threading.current_thread() is threading.main_thread():
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        def terminate(_signum, _frame):
            # The app-server owns a separate process group. Unwind even while a
            # classifier is blocked; otherwise terminating this proxy or its
            # parent group would orphan the server. Ignore repeated TERM during
            # bounded cleanup so it cannot interrupt reaping the child.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, terminate)
    def write(data):
        outgoing.write(data)
        outgoing.flush()
    try:
        while selector.get_map():
            for key, _ in selector.select(timeout=1):
                source = key.data
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    if source == 'client':
                        # EOF is an intentional shutdown, not a hung model session.
                        return 0
                    if buffers[source]:
                        write(buffers[source])
                    return child.wait(timeout=3)
                buffers[source] += chunk
                if len(buffers[source]) > MAX_FRAME and b'\n' not in buffers[source]:
                    errors.write(b'Routed app-server frame exceeds limit\n')
                    errors.flush()
                    return 2
                while b'\n' in buffers[source]:
                    line, buffers[source] = buffers[source].split(b'\n', 1)
                    if len(line) > MAX_FRAME:
                        errors.write(b'Routed app-server frame exceeds limit\n')
                        errors.flush()
                        return 2
                    if source == 'server':
                        write(line + b'\n')
                        continue
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError):
                        write(_encode({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}}))
                        continue
                    if isinstance(message, dict) and message.get('method') == 'turn/start':
                        ident = message.get('id')
                        if type(ident) in (str, int):
                            marker = (type(ident), ident)
                            if marker in seen_turn_ids:
                                write(_encode({'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32001, 'message': 'Duplicate routed turn ID; not forwarded'}}))
                                continue
                            if len(seen_turn_ids) >= 10000:
                                write(_encode({'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32001, 'message': 'Session routing limit reached'}}))
                                continue
                            seen_turn_ids.add(marker)
                        routed, error, receipt = route_turn(message, decide=decide, authorize=authorize)
                        if receipt:
                            errors.write(_encode(receipt))
                            errors.flush()
                        if error:
                            write(_encode(error))
                            continue
                        line = _encode(routed).rstrip(b'\n')
                    child.stdin.write(line + b'\n')
                    child.stdin.flush()
            if child.poll() is not None and not any(k.data == 'server' for k in selector.get_map().values()):
                return child.returncode
    except (BrokenPipeError, KeyboardInterrupt):
        return 1
    finally:
        selector.close()
        try:
            _stop(child)
            for pipe in (child.stdin, child.stdout):
                if pipe:
                    pipe.close()
        finally:
            if previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


def prompt_once(binary, prompt, *, timeout=600):
    """One native read-only app-server turn, via this same routing proxy.

    No automatic approval: server requests are rejected; useful for a text smoke.
    Main interactive clients should connect the stdio proxy directly.
    """
    command = [sys.executable, str(Path(__file__).resolve()), '--binary', binary]
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    buffer = b''
    deadline = time.monotonic() + timeout
    final_text = None
    def send(value):
        child.stdin.write(_encode(value))
        child.stdin.flush()
    try:
        send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
              'params': {'clientInfo': {'name': 'strategist_routed', 'version': '1.0'}}})
        while time.monotonic() < deadline:
            if not selector.select(timeout=min(1, max(0, deadline-time.monotonic()))):
                if child.poll() is not None:
                    raise RuntimeError('App-server closed before completion')
                continue
            chunk = os.read(child.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('App-server closed before completion')
            buffer += chunk
            if len(buffer) > MAX_FRAME and b'\n' not in buffer:
                raise RuntimeError('App-server frame exceeds limit')
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                message = json.loads(line)
                if 'method' in message and 'id' in message:
                    # Do not infer approval response shapes; a JSON-RPC error
                    # safely rejects any unsupported server request.
                    send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Interactive server requests disabled in one-shot mode'}})
                elif message.get('error'):
                    raise RuntimeError('App-server request rejected: ' + str(message['error'].get('code')))
                elif message.get('id') == 1:
                    send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})
                    send({'jsonrpc': '2.0', 'id': 2, 'method': 'thread/start', 'params': {
                        'ephemeral': True, 'cwd': str(Path.cwd()), 'approvalPolicy': 'never', 'sandbox': 'read-only'}})
                elif message.get('id') == 2:
                    thread = message['result']['thread']['id']
                    send({'jsonrpc': '2.0', 'id': 3, 'method': 'turn/start', 'params': {
                        'threadId': thread, 'input': [{'type': 'text', 'text': prompt}]}})
                elif message.get('method') == 'item/completed':
                    item = message.get('params', {}).get('item', {})
                    if item.get('type') == 'agentMessage':
                        final_text = item.get('text')
                elif message.get('method') == 'turn/completed':
                    turn = message.get('params', {}).get('turn', {})
                    return {'status': turn.get('status'), 'text': final_text, 'turn_id': turn.get('id')}
        raise TimeoutError('Routed turn deadline exceeded')
    finally:
        selector.close()
        if child.stdin:
            child.stdin.close()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _stop(child)
        if child.stdout:
            child.stdout.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--prompt-file', type=Path)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('server_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.binary.is_absolute() or not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        parser.error('--binary must be an absolute existing executable')
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    try:
        if args.prompt_file:
            if args.server_args:
                parser.error('server_args cannot be combined with --prompt-file')
            result = prompt_once(str(args.binary), args.prompt_file.read_text(), timeout=args.timeout)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['status'] == 'completed' else 3
        forwarded = args.server_args[1:] if args.server_args[:1] == ['--'] else args.server_args
        # Never accept websocket/listen changes: this adapter implements stdio.
        if '--startup-sign-out' in forwarded:
            parser.error('Account mutation is not supported by this router')
        if any(a == '--listen' or a.startswith('--listen=') for a in forwarded):
            parser.error('Only stdio app-server transport is supported')
        return proxy([str(args.binary), 'app-server', '--stdio', *forwarded])
    except (OSError, ValueError, RuntimeError, TimeoutError):
        print('Routed Codex failed; no retry or account fallback', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
