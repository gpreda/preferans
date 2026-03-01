"""Single entry point — starts all services.

Usage:
    python3 run.py

Environment variables (all optional):
    ENGINE_PORT        port for the bidding engine service  (default 3001)
    FLASK_PORT         port for the web server              (default 3000)
    FLASK_HOST         bind address                         (default 0.0.0.0)
    FLASK_DEBUG        1 = enable Flask reloader            (default 1)
"""

import atexit
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Start the engine as a separate subprocess so it gets its own reloader.
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

    time.sleep(0.5)   # give the engine a moment to bind its port

from preferans_server import app as web_app

host  = os.environ.get('FLASK_HOST',  '0.0.0.0')
port  = int(os.environ.get('FLASK_PORT',  '3000'))
debug = os.environ.get('FLASK_DEBUG', '1') == '1'

web_app.run(host=host, port=port, debug=debug)
