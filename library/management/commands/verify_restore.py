import hashlib
import json
from pathlib import Path
import re
from django.core.management.base import BaseCommand, CommandError
from psycopg import connect, sql
from library.backup import config, fingerprints, pg_tool


class Command(BaseCommand):
    help = 'Restore into a NEW shelfs_restore_* database and compare every row.'
    def add_arguments(self, parser):
        parser.add_argument('archive', type=Path)
        parser.add_argument('--database', required=True)

    def handle(self, *args, **options):
        path, target = options['archive'].resolve(), options['database']
        if not re.fullmatch(r'shelfs_restore_[a-z0-9_]{1,40}', target) or target == config()['dbname']:
            raise CommandError('Використайте нову окрему базу з назвою shelfs_restore_*.')
        manifest = json.loads(Path(str(path)+'.json').read_text(encoding='utf-8'))
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['archive_sha256']:
            raise CommandError('Контрольна сума архіву не збігається.')
        with connect(**config('postgres'), autocommit=True) as conn:
            if conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', [target]).fetchone():
                raise CommandError('Цільова база вже існує. Вона не буде перезаписана.')
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(target)))
        pg_tool('pg_restore', target, ['--exit-on-error', '--single-transaction', '--no-owner', '--no-privileges', path])
        with connect(**config(target)) as conn:
            actual = fingerprints(conn)
        if actual != manifest['tables']:
            raise CommandError('Відновлено, але дані не збігаються з копією. Оригінальну базу не змінено.')
        report = {'database': target, 'verified_tables': len(actual), 'verified_rows': sum(t['rows'] for t in actual.values()), 'result': 'passed'}
        Path(str(path)+'.restore.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'Відновлення перевірено: {len(actual)} таблиць; база {target}.'))
