"""Local server + once-per-minute scheduler. Ctrl+C stops both.

The production scheduler should invoke manage.py process_library separately.
"""
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
stop = threading.Event()


def scheduler():
    while not stop.is_set():
        try:
            subprocess.run([sys.executable, str(ROOT / 'manage.py'), 'process_library'], cwd=ROOT, timeout=55, check=False)
        except subprocess.TimeoutExpired:
            print('Scheduler cycle exceeded 55 seconds; inspect mail settings.', flush=True)
        stop.wait(60)


if __name__ == '__main__':
    os.environ.setdefault('PYTHONUTF8', '1')
    worker = threading.Thread(target=scheduler, daemon=True)
    server = subprocess.Popen([sys.executable, str(ROOT / 'manage.py'), 'runserver', '127.0.0.1:8000', '--noreload'], cwd=ROOT)
    worker.start()
    try:
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=15)
        worker.join(timeout=56)
