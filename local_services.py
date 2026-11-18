"""Start private, loopback-only PostgreSQL and Mailpit for the defence demo."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / '.packages'))
DATA = ROOT / 'data'
BIN = ROOT / '.runtime/pgsql/bin'
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def command(args, **kwargs):
    return subprocess.run([str(a) for a in args], creationflags=HIDDEN, **kwargs)


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def start():
    import psycopg
    DATA.mkdir(exist_ok=True)
    if not (BIN / 'pg_ctl.exe').exists():
        raise SystemExit('PostgreSQL runtime missing. Run setup-runtime.ps1 first.')
    credentials = DATA / 'local-services.json'
    if credentials.exists():
        config = json.loads(credentials.read_text(encoding='utf-8'))
    else:
        config = {'owner_password': secrets.token_urlsafe(32), 'app_password': secrets.token_urlsafe(32)}
        with credentials.open('x', encoding='utf-8') as stream:
            json.dump(config, stream)
    cluster = DATA / 'postgres'
    if not (cluster / 'PG_VERSION').exists():
        password_file = DATA / 'init-password.tmp'
        password_file.write_text(config['owner_password'], encoding='utf-8')
        try:
            command([BIN/'initdb.exe', '-D', cluster, '-U', 'shelfs_owner', '--auth=scram-sha-256',
                     '--pwfile', password_file, '--locale-provider=icu', '--icu-locale=uk-UA',
                     '--locale=C', '--encoding=UTF8'], check=True)
        finally:
            password_file.unlink(missing_ok=True)
        with (cluster/'postgresql.conf').open('a', encoding='utf-8') as stream:
            stream.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\n")
    # A process-status query can fail across Windows process security contexts.
    # Probe the loopback endpoint, then authenticate and verify the cluster.
    if not listening(55432):
        command([BIN/'pg_ctl.exe', '-D', cluster, '-l', DATA/'postgres.log', '-w', 'start'], check=True)
    with psycopg.connect(host='127.0.0.1', port=55432, dbname='postgres', user='shelfs_owner',
                         password=config['owner_password'], autocommit=True, connect_timeout=5) as conn:
        actual_cluster = Path(conn.execute('SHOW data_directory').fetchone()[0]).resolve()
        if actual_cluster != cluster.resolve():
            raise SystemExit('Port 55432 is used by another PostgreSQL cluster.')
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='shelfs'").fetchone():
            from psycopg import sql
            conn.execute(sql.SQL('CREATE ROLE shelfs LOGIN CREATEDB PASSWORD {}').format(sql.Literal(config['app_password'])))
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='shelfs'").fetchone():
            conn.execute('CREATE DATABASE shelfs OWNER shelfs')
    env_file = ROOT / '.env'
    if not env_file.exists():
        env_file.write_text('\n'.join([
            'DEBUG=1', 'SECRET_KEY='+secrets.token_urlsafe(64), 'DB_ENGINE=postgresql',
            'DB_HOST=127.0.0.1', 'DB_PORT=55432', 'DB_NAME=shelfs', 'DB_USER=shelfs',
            'DB_PASSWORD='+config['app_password'], 'SITE_URL=http://127.0.0.1:8000',
            'EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend', 'EMAIL_HOST=127.0.0.1',
            'EMAIL_PORT=1025', 'EMAIL_USE_TLS=0', 'EMAIL_USE_SSL=0',
            'DEFAULT_FROM_EMAIL=library@shelfs.local', 'LOCAL_SERVICES=1', '',
        ]), encoding='utf-8')
    if not listening(1025):
        mailpit = ROOT / '.runtime/mailpit/mailpit.exe'
        if not mailpit.exists():
            raise SystemExit('Mailpit runtime missing. Run setup-runtime.ps1 first.')
        with (DATA/'mailpit.log').open('ab') as log:
            child = subprocess.Popen([str(mailpit), '--listen', '127.0.0.1:8025', '--smtp', '127.0.0.1:1025',
                '--database', str(DATA/'mailpit.db'), '--disable-version-check', '--label', 'ShelfS local mail',
                '--allowed-hosts', '127.0.0.1,localhost', '--max', '1000'],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=HIDDEN)
        (DATA/'mailpit.pid').write_text(str(child.pid), encoding='ascii')
        for _ in range(40):
            if listening(1025) and listening(8025):
                break
            if child.poll() is not None:
                raise SystemExit('Mailpit failed; see data/mailpit.log.')
            time.sleep(.25)
        else:
            raise SystemExit('Mailpit startup timeout; see data/mailpit.log.')
    print('PostgreSQL: 127.0.0.1:55432; local mailbox: http://127.0.0.1:8025')


if __name__ == '__main__':
    start()
    if '--stay' in sys.argv:
        while True:
            time.sleep(30)
