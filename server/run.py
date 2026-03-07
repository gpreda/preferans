"""Single entry point — starts all services.

Usage:
    python3 run.py

Environment variables (all optional):
    ENGINE_PORT        port for the bidding engine service  (default 3001)
    AGENT_PORT         port for the Claude agent service    (default 3002)
    FLASK_PORT         port for the web server              (default 3000)
    FLASK_HOST         bind address                         (default 0.0.0.0)
    FLASK_DEBUG        1 = enable Flask reloader            (default 1)
"""

import atexit
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Start the engine and agent as separate subprocesses so they get their own
# reloaders and survive independently.
# Guard: only in the parent process, not in Flask reloader's child.
if not os.environ.get('WERKZEUG_RUN_MAIN'):
    import subprocess
    import time

    engine_port = int(os.environ.get('ENGINE_PORT', '3001'))
    _engine = subprocess.Popen(
        [sys.executable, 'game_engine_service.py'],
        env={**os.environ, 'ENGINE_PORT': str(engine_port)},
    )
    atexit.register(_engine.terminate)

    agent_port = int(os.environ.get('AGENT_PORT', '3002'))
    _agent = subprocess.Popen(
        [sys.executable, 'agent_service.py'],
        env={**os.environ, 'AGENT_PORT': str(agent_port)},
    )
    atexit.register(_agent.terminate)

    time.sleep(0.5)   # give services a moment to bind their ports

from preferans_server import app as web_app

host  = os.environ.get('FLASK_HOST',  '0.0.0.0')
port  = int(os.environ.get('FLASK_PORT',  '3000'))
debug = os.environ.get('FLASK_DEBUG', '1') == '1'

web_app.run(host=host, port=port, debug=debug)
