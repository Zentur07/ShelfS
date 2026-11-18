import hashlib
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from psycopg import connect
from library.backup import config, fingerprints, pg_tool


class Command(BaseCommand):
    help = 'Create a consistent PostgreSQL archive with verification manifest.'
    def add_arguments(self, parser):
        parser.add_argument('--output', type=Path)

    def handle(self, *args, **options):
        path = options['output'] or settings.DATA_DIR/'backups'/f'shelfs-{timezone.now():%Y%m%d-%H%M%S-%f}.dump'
        path = path.resolve()
        manifest_path = Path(str(path)+'.json')
        if path.exists() or manifest_path.exists():
            raise CommandError('Файл уже існує. Оберіть нове ім’я резервної копії.')
        path.parent.mkdir(parents=True, exist_ok=True)
        with connect(**config()) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            snapshot = conn.execute('SELECT pg_export_snapshot()').fetchone()[0]
            expected = fingerprints(conn)
            pg_tool('pg_dump', config()['dbname'], ['--format=custom', '--no-owner', '--no-privileges', '--snapshot', snapshot, '--file', path])
        manifest = {'created_at': timezone.now().isoformat(), 'archive_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'tables': expected}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'Резервна копія: {path}'))
