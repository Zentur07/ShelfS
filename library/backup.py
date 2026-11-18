"""Consistent PostgreSQL archive and verification in a new, separate database."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from django.conf import settings
from django.core.management.base import CommandError
from psycopg import connect, sql


def config(name=None):
    db = settings.DATABASES['default']
    if db['ENGINE'] != 'django.db.backends.postgresql':
        raise CommandError('Резервне копіювання потребує PostgreSQL.')
    return dict(dbname=name or db['NAME'], host=db['HOST'], port=db['PORT'],
                user=db['USER'], password=db['PASSWORD'], connect_timeout=5)


def fingerprints(conn):
    tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
    result = {}
    for (table,) in tables:
        digest = hashlib.sha256()
        rows = conn.execute(sql.SQL('SELECT to_jsonb(t)::text FROM public.{} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(table))).fetchall()
        for (row,) in rows:
            digest.update(row.encode('utf-8') + b'\n')
        result[table] = {'rows': len(rows), 'sha256': digest.hexdigest()}
    return result


def pg_tool(name, database, arguments):
    binary = settings.BASE_DIR / '.runtime/pgsql/bin' / (name + '.exe')
    executable = str(binary) if binary.exists() else shutil.which(name)
    if not executable:
        raise CommandError(f'Не знайдено {name}. Установіть засоби PostgreSQL.')
    db = config(database)
    env = os.environ.copy()
    env['PGPASSWORD'] = db['password']
    env['PGCONNECT_TIMEOUT'] = '5'
    run = subprocess.run([executable, '-h', db['host'], '-p', str(db['port']), '-U', db['user'],
                          '-d', database, *map(str, arguments)], env=env, capture_output=True,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if run.returncode:
        # Do not echo provider errors or connection secrets.
        raise CommandError(f'{name} завершився з кодом {run.returncode}. Перевірте доступ і журнал PostgreSQL.')
