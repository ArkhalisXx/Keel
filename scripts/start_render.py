"""Supervise the web server and one bounded database worker on Render Free."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent


def main():
    required = ['DJANGO_SECRET_KEY', 'DATABASE_URL', 'AWS_STORAGE_BUCKET_NAME',
                'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY']
    if any(not os.environ.get(name) for name in required):
        raise SystemExit('Configure the secret, PostgreSQL URL, and private bucket credentials before deployment.')
    if os.environ.get('JOB_BACKEND') != 'database' or os.environ.get('DJANGO_DEBUG') != '0':
        raise SystemExit('Render requires JOB_BACKEND=database and DJANGO_DEBUG=0.')
    os.chdir(ROOT)
    subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput'], check=True)
    subprocess.run([sys.executable, 'manage.py', 'collectstatic', '--noinput'], check=True)
    children = []
    stopping = False

    def stop(signum=None, frame=None):
        nonlocal stopping
        stopping = True
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for command in [
            ['gunicorn', 'config.wsgi:application', '--bind', '0.0.0.0:' + os.environ.get('PORT', '8000'),
             '--workers', '1', '--threads', '2', '--timeout', '90', '--access-logfile', '-', '--error-logfile', '-'],
            [sys.executable, 'manage.py', 'run_database_worker'],
        ]:
            children.append(subprocess.Popen(command, start_new_session=True))
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(1)
        return 0 if stopping else 1
    finally:
        stop()
        deadline = time.monotonic() + 8
        for child in children:
            try:
                child.wait(timeout=max(.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()


if __name__ == '__main__':
    sys.exit(main())
