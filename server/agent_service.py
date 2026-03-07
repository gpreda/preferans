"""Standalone Claude agent service — runs independently of the web server.

Listens on port 3002 (configurable via AGENT_PORT env var).
State persists to ../logs/agent_state.json so it survives restarts of any service.
"""

from flask import Flask, jsonify, request
import os
import subprocess
import threading
import time
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_STATE_FILE = os.path.join(BASE_DIR, 'logs', 'agent_state.json')
os.makedirs(os.path.dirname(AGENT_STATE_FILE), exist_ok=True)

app = Flask(__name__)

_AGENT_STATUS_MSGS = [
    'Claude is thinking...',
    'Reading game state...',
    'Analyzing player logic...',
    'Formulating response...',
]

# Track the running subprocess so we can check if it's alive
_agent_proc = None
_agent_lock = threading.Lock()


def _read_state():
    try:
        with open(AGENT_STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _write_state(data):
    tmp = AGENT_STATE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, AGENT_STATE_FILE)


def _update_state(**kwargs):
    st = _read_state()
    st.update(kwargs)
    _write_state(st)


def _is_agent_alive():
    """Check if the tracked agent subprocess is still running."""
    global _agent_proc
    with _agent_lock:
        if _agent_proc is None:
            return False
        if _agent_proc.poll() is None:
            return True
        _agent_proc = None
        return False


def _run_agent(prompt):
    global _agent_proc
    try:
        _update_state(status_text='Starting Claude...')

        env = {k: v for k, v in os.environ.items() if k != 'CLAUDECODE'}
        proc = subprocess.Popen(
            ['claude', '--dangerously-skip-permissions', '-p', prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=BASE_DIR,
            env=env,
            text=True,
        )
        with _agent_lock:
            _agent_proc = proc

        status_idx = 0
        while proc.poll() is None:
            _update_state(status_text=_AGENT_STATUS_MSGS[
                status_idx % len(_AGENT_STATUS_MSGS)])
            status_idx += 1
            time.sleep(2.0)

        stdout, stderr = proc.communicate(timeout=5)

        with _agent_lock:
            _agent_proc = None

        if proc.returncode == 0:
            _update_state(status='complete', status_text='',
                          response=stdout.strip(), completed_at=time.time())
        else:
            _update_state(status='error', status_text='',
                          error=stderr.strip() or f'Exit code {proc.returncode}',
                          completed_at=time.time())

    except subprocess.TimeoutExpired:
        proc.kill()
        with _agent_lock:
            _agent_proc = None
        _update_state(status='error', status_text='',
                      error='Claude subprocess timed out',
                      completed_at=time.time())
    except Exception as e:
        with _agent_lock:
            _agent_proc = None
        _update_state(status='error', status_text='',
                      error=str(e), completed_at=time.time())


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json() or {}
    prompt = data.get('prompt', '').strip()
    question = data.get('question', '').strip()
    if not prompt:
        return jsonify({'error': 'No prompt provided'}), 400

    # Check if already running (use live process check, not just file state)
    cur = _read_state()
    if cur.get('status') == 'running':
        if _is_agent_alive():
            return jsonify({'error': 'Agent is already running'}), 409
        # Process is dead — clear stale state
        _update_state(status='error', status_text='',
                      error='Agent process died unexpectedly',
                      completed_at=time.time())

    _write_state({
        'status': 'running',
        'status_text': 'Sending to Claude...',
        'question': question,
        'response': None,
        'error': None,
        'started_at': time.time(),
        'completed_at': None,
    })

    t = threading.Thread(target=_run_agent, args=(prompt,), daemon=False)
    t.start()

    return jsonify({'ok': True})


@app.route('/status')
def status():
    st = _read_state()
    if not st:
        return jsonify({'status': 'idle', 'status_text': '',
                        'question': None, 'response': None, 'error': None})

    # If state says running but process is dead, fix it
    if st.get('status') == 'running' and not _is_agent_alive():
        st['status'] = 'error'
        st['error'] = 'Agent process died unexpectedly'
        st['status_text'] = ''
        st['completed_at'] = time.time()
        _write_state(st)

    return jsonify({
        'status': st.get('status', 'idle'),
        'status_text': st.get('status_text', ''),
        'question': st.get('question'),
        'response': st.get('response'),
        'error': st.get('error'),
    })


if __name__ == '__main__':
    port = int(os.environ.get('AGENT_PORT', '3002'))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
