import secrets
from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from library.models import Author, Book, Copy, Genre, User

class Command(BaseCommand):
    help = 'Create local demonstration books and accounts (never resets existing passwords).'

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Demo data creation is allowed only with DEBUG=1.')
        group, _ = Group.objects.get_or_create(name='Бібліотекарі')
        catalog_models = ['author', 'genre', 'book', 'copy']
        permissions = Permission.objects.filter(content_type__app_label='library')
        group.permissions.set([p for p in permissions if p.codename.startswith('view_') or
                               (p.content_type.model in catalog_models and p.codename.startswith(('add_', 'change_')))])
        credentials = []
        for email, first, staff in [('reader@example.com', 'Олена', False), ('librarian@example.com', 'Ірина', True)]:
            if not User.objects.filter(email=email).exists():
                password = secrets.token_urlsafe(15)
                user = User.objects.create_user(email, password, first_name=first, last_name='Демонстраційна', email_verified=True, is_staff=staff)
                if staff:
                    user.groups.add(group)
                credentials.append(f'{email}\n{password}\n')
        samples = [
            ('Лісова пісня', 'Леся Українка', 'Драма', 'Драма-феєрія про Мавку, Лукаша та зустріч людського і лісового світів.'),
            ('Тигролови', 'Іван Багряний', 'Пригоди', 'Роман про свободу, гідність і силу людини перед випробуваннями.'),
            ('Місто', 'Валер’ян Підмогильний', 'Роман', 'Історія Степана Радченка та його життя в Києві.'),
            ('Кобзар', 'Тарас Шевченко', 'Поезія', 'Збірка поетичних творів Тараса Шевченка.'),
            ('Захар Беркут', 'Іван Франко', 'Історичний роман', 'Повість про громаду Тухлі та боротьбу за її свободу.'),
            ('Кайдашева сім’я', 'Іван Нечуй-Левицький', 'Повість', 'Соціально-побутова повість про життя української родини.'),
        ]
        for number, (title, author_name, genre_name, description) in enumerate(samples, 1):
            author, _ = Author.objects.get_or_create(name=author_name)
            genre, _ = Genre.objects.get_or_create(name=genre_name)
            book, _ = Book.objects.get_or_create(title=title, defaults={'description': description})
            book.authors.add(author)
            book.genres.add(genre)
            for index in range(1, 3):
                Copy.objects.get_or_create(inventory_number=f'SH-{number:03}-{index}', defaults={'book': book})
        if credentials:
            path = settings.BASE_DIR / '.demo-credentials.txt'
            with path.open('a', encoding='utf-8') as stream:
                stream.write('\n'.join(credentials))
            self.stdout.write(f'Demo login details saved to {path}')
        self.stdout.write(self.style.SUCCESS('Demo ready: 6 books, 12 copies. Existing data preserved.'))
