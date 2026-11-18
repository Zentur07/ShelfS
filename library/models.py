import uuid
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Укажіть електронну пошту.')
        user = self.model(email=email.strip().lower(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('email_verified', True)
        if not extra_fields['is_staff'] or not extra_fields['is_superuser']:
            raise ValueError('Superuser must have both staff and superuser privileges')
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField('Електронна пошта', unique=True)
    patronymic = models.CharField('По батькові', max_length=150, blank=True)
    email_verified = models.BooleanField('Пошту підтверджено', default=False)
    confirmation_nonce = models.UUIDField(default=uuid.uuid4, editable=False)
    confirmation_sent_at = models.DateTimeField('Останній лист підтвердження', null=True, blank=True)
    borrowing_blocked = models.BooleanField('Бронювання обмежено', default=False)
    block_reason = models.CharField('Причина обмеження', max_length=300, blank=True)
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']
    objects = UserManager()

    class Meta:
        verbose_name = 'Користувач'
        verbose_name_plural = 'Користувачі'
        constraints = [models.UniqueConstraint(Lower('email'), name='user_email_case_unique')]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.get_full_name()} ({self.email})'.strip()


class Author(models.Model):
    name = models.CharField('Ім’я автора', max_length=200, unique=True)
    class Meta:
        ordering = ['name']
        verbose_name = 'Автор'
        verbose_name_plural = 'Автори'
    def __str__(self):
        return self.name


class Genre(models.Model):
    name = models.CharField('Назва жанру', max_length=100, unique=True)
    class Meta:
        ordering = ['name']
        verbose_name = 'Жанр'
        verbose_name_plural = 'Жанри'
    def __str__(self):
        return self.name


class Book(models.Model):
    title = models.CharField('Назва', max_length=250)
    authors = models.ManyToManyField(Author, related_name='books', verbose_name='Автори')
    genres = models.ManyToManyField(Genre, related_name='books', verbose_name='Жанри')
    description = models.TextField('Опис', blank=True)
    class Meta:
        ordering = ['title', 'pk']
        verbose_name = 'Книга'
        verbose_name_plural = 'Книги'
    def __str__(self):
        return self.title


class Copy(models.Model):
    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name='copies', verbose_name='Книга')
    inventory_number = models.CharField('Інвентарний номер', max_length=50, unique=True)
    is_active = models.BooleanField('Доступний для обслуговування', default=True)
    class Meta:
        ordering = ['inventory_number']
        verbose_name = 'Примірник'
        verbose_name_plural = 'Примірники'
    def __str__(self):
        return f'{self.inventory_number} — {self.book}'


class Reservation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Активне'
        CANCELLED = 'cancelled', 'Скасоване'
        EXPIRED = 'expired', 'Строк минув'
        FULFILLED = 'fulfilled', 'Книгу видано'
    reader = models.ForeignKey(User, on_delete=models.PROTECT, related_name='reservations', verbose_name='Читач')
    copy = models.ForeignKey(Copy, on_delete=models.PROTECT, related_name='reservations', verbose_name='Примірник')
    created_at = models.DateTimeField('Створено', default=timezone.now)
    expires_at = models.DateTimeField('Діє до')
    status = models.CharField('Статус', max_length=12, choices=Status, default=Status.ACTIVE)
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Бронювання'
        verbose_name_plural = 'Бронювання'
        constraints = [
            models.UniqueConstraint(fields=['copy'], condition=Q(status='active'), name='one_active_reservation_per_copy'),
            models.CheckConstraint(condition=Q(expires_at__gt=models.F('created_at')), name='reservation_positive_period'),
        ]
    @property
    def effective_status(self):
        if self.status == self.Status.ACTIVE and self.expires_at <= timezone.now():
            return 'Строк минув'
        return self.get_status_display()


class Loan(models.Model):
    reader = models.ForeignKey(User, on_delete=models.PROTECT, related_name='loans', verbose_name='Читач')
    copy = models.ForeignKey(Copy, on_delete=models.PROTECT, related_name='loans', verbose_name='Примірник')
    issued_at = models.DateTimeField('Видано', default=timezone.now)
    due_date = models.DateField('Повернути до')
    returned_at = models.DateTimeField('Повернуто', null=True, blank=True)
    class Meta:
        ordering = ['-issued_at']
        verbose_name = 'Видача'
        verbose_name_plural = 'Видачі'
        constraints = [models.UniqueConstraint(fields=['copy'], condition=Q(returned_at__isnull=True), name='one_open_loan_per_copy')]
    @property
    def overdue(self):
        return self.returned_at is None and self.due_date < timezone.localdate()


class Review(models.Model):
    reader = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name='Читач')
    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name='reviews', verbose_name='Книга')
    text = models.TextField('Ваш відгук', max_length=2000)
    created_at = models.DateTimeField('Створено', default=timezone.now)
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Відгук'
        verbose_name_plural = 'Відгуки'


class Notification(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name='notifications', verbose_name='Видача')
    kind = models.CharField('Тип', max_length=10, choices=[('reminder', 'Нагадування'), ('overdue', 'Прострочення')])
    period = models.DateField('Період сповіщення')
    scheduled_at = models.DateTimeField('Заплановано на', default=timezone.now)
    recipient = models.EmailField('Одержувач')
    state = models.CharField('Стан надсилання', max_length=10, default='pending', choices=[('pending', 'Очікує'), ('sent', 'Надіслано'), ('failed', 'Помилка'), ('cancelled', 'Скасовано')])
    attempts = models.PositiveIntegerField('Кількість спроб', default=0)
    last_error = models.TextField('Остання помилка', blank=True)
    sent_at = models.DateTimeField('Надіслано', null=True, blank=True)
    class Meta:
        verbose_name = 'Повідомлення'
        verbose_name_plural = 'Повідомлення'
        constraints = [models.UniqueConstraint(fields=['loan', 'kind', 'period'], name='unique_notification_period')]
