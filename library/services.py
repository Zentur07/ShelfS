"""Library rules live here, independently of HTML views and forms.

Lock order is User -> Copy -> Reservation/Loan. Every circulation operation
locks the same Copy row so PostgreSQL serializes competing requests.
SQLite is only a single-user learning/demo mode; its row locks are no-ops.
"""
import logging
import uuid
from datetime import datetime, time, timedelta
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.urls import reverse
from django.utils import timezone
from .models import Copy, Loan, Notification, Reservation, User

logger = logging.getLogger(__name__)


def require_staff(actor):
    if not actor.is_authenticated or not actor.is_active or not actor.is_staff:
        raise PermissionDenied('Ця операція доступна лише бібліотекарю.')


def available_copies():
    active_reservations = Reservation.objects.filter(
        copy_id=OuterRef('pk'), status=Reservation.Status.ACTIVE,
        expires_at__gt=timezone.now(),
    )
    open_loans = Loan.objects.filter(copy_id=OuterRef('pk'), returned_at__isnull=True)
    return Copy.objects.filter(is_active=True).annotate(
        reserved=Exists(active_reservations), on_loan=Exists(open_loans),
    ).filter(reserved=False, on_loan=False)


def expire_copy_reservations(copy):
    # Caller holds the Copy lock. An expiry never edits or closes a Loan.
    return Reservation.objects.filter(
        copy=copy, status=Reservation.Status.ACTIVE, expires_at__lte=timezone.now(),
    ).update(status=Reservation.Status.EXPIRED)


@transaction.atomic
def reserve(reader, book_id):
    reader = User.objects.select_for_update().get(pk=reader.pk)
    if not reader.is_active or not reader.email_verified:
        raise ValidationError('Для бронювання підтвердьте електронну пошту.')
    if reader.borrowing_blocked:
        raise ValidationError(f'Бронювання обмежено: {reader.block_reason}')
    # Repeated clicks should return the original active reservation.
    existing = Reservation.objects.filter(
        reader=reader, copy__book_id=book_id, status=Reservation.Status.ACTIVE,
        expires_at__gt=timezone.now(),
    ).first()
    if existing:
        return existing
    if Loan.objects.filter(reader=reader, copy__book_id=book_id, returned_at__isnull=True).exists():
        raise ValidationError('Ви вже користуєтеся примірником цієї книги.')
    for copy in Copy.objects.select_for_update().filter(book_id=book_id, is_active=True).order_by('pk'):
        expire_copy_reservations(copy)
        if copy.reservations.filter(status=Reservation.Status.ACTIVE).exists():
            continue
        if copy.loans.filter(returned_at__isnull=True).exists():
            continue
        now = timezone.now()
        return Reservation.objects.create(
            reader=reader, copy=copy, created_at=now,
            expires_at=now + timedelta(hours=settings.RESERVATION_HOURS),
        )
    raise ValidationError('Зараз немає вільних примірників цієї книги.')


@transaction.atomic
def cancel_reservation(reader, reservation_id):
    User.objects.select_for_update().get(pk=reader.pk)
    record = Reservation.objects.get(pk=reservation_id)
    if record.reader_id != reader.pk:
        raise PermissionDenied('Ви можете скасувати лише власне бронювання.')
    copy = Copy.objects.select_for_update().get(pk=record.copy_id)
    expire_copy_reservations(copy)
    record.refresh_from_db()
    if record.status == Reservation.Status.ACTIVE:
        record.status = Reservation.Status.CANCELLED
        record.save(update_fields=['status'])
    return record


@transaction.atomic
def issue_loan(actor, reader_id, copy_id, due_date):
    require_staff(actor)
    reader = User.objects.select_for_update().get(pk=reader_id)
    copy = Copy.objects.select_for_update().get(pk=copy_id)
    if not reader.is_active:
        raise ValidationError('Обліковий запис читача неактивний.')
    if not copy.is_active:
        raise ValidationError('Примірник вилучено з обслуговування.')
    if due_date < timezone.localdate():
        raise ValidationError('Дата повернення не може бути в минулому.')
    expire_copy_reservations(copy)
    if copy.loans.filter(returned_at__isnull=True).exists():
        raise ValidationError('Цей примірник уже видано.')
    reservation = copy.reservations.filter(status=Reservation.Status.ACTIVE).first()
    if reservation and reservation.reader_id != reader.pk:
        raise ValidationError('Примірник заброньовано іншим читачем.')
    loan = Loan.objects.create(reader=reader, copy=copy, due_date=due_date)
    if reservation:
        reservation.status = Reservation.Status.FULFILLED
        reservation.save(update_fields=['status'])
    return loan


@transaction.atomic
def return_loan(actor, loan_id):
    require_staff(actor)
    record = Loan.objects.get(pk=loan_id)
    Copy.objects.select_for_update().get(pk=record.copy_id)
    record = Loan.objects.select_for_update().get(pk=loan_id)
    if record.returned_at is None:
        record.returned_at = timezone.now()
        record.save(update_fields=['returned_at'])
        record.notifications.filter(state__in=['pending', 'failed']).update(state='cancelled')
    return record


@transaction.atomic
def set_restriction(actor, reader_id, blocked, reason=''):
    require_staff(actor)
    reader = User.objects.select_for_update().get(pk=reader_id)
    if blocked and not reason.strip():
        raise ValidationError('Укажіть причину обмеження.')
    reader.borrowing_blocked = blocked
    reader.block_reason = reason.strip() if blocked else ''
    reader.save(update_fields=['borrowing_blocked', 'block_reason'])


def confirmation_token(user):
    return signing.dumps(
        {'uid': user.pk, 'email': user.email, 'nonce': str(user.confirmation_nonce)},
        salt='shelfs.email-confirmation',
    )


@transaction.atomic
def confirm_email(token):
    try:
        payload = signing.loads(token, salt='shelfs.email-confirmation', max_age=settings.CONFIRMATION_MAX_AGE)
        user = User.objects.select_for_update().get(pk=payload['uid'], email=payload['email'])
        if user.email_verified:
            return user
        if str(user.confirmation_nonce) != payload['nonce']:
            raise signing.BadSignature()
    except (signing.BadSignature, User.DoesNotExist, KeyError, TypeError) as exc:
        raise ValidationError('Посилання недійсне або строк його дії минув. Запросіть новий лист.') from exc
    user.email_verified = True
    user.confirmation_nonce = uuid.uuid4()
    user.save(update_fields=['email_verified', 'confirmation_nonce'])
    return user


@transaction.atomic
def send_confirmation(user):
    user = User.objects.select_for_update().get(pk=user.pk)
    if user.email_verified:
        return False
    now = timezone.now()
    if user.confirmation_sent_at and now - user.confirmation_sent_at < timedelta(seconds=60):
        raise ValidationError('Повторний лист можна запросити через хвилину.')
    user.confirmation_nonce = uuid.uuid4()
    link = settings.SITE_URL + reverse('confirm-email', args=[confirmation_token(user)])
    try:
        count = send_mail('Підтвердження пошти ShelfS', f'Підтвердіть вашу адресу:\n{link}\nПосилання діє одну годину.', settings.DEFAULT_FROM_EMAIL, [user.email])
        if count != 1:
            raise RuntimeError('SMTP did not accept the message')
    except Exception as exc:
        logger.error('Confirmation delivery failed for user %s: %s', user.pk, type(exc).__name__)
        return False
    user.confirmation_sent_at = now
    user.save(update_fields=['confirmation_sent_at', 'confirmation_nonce'])
    return True


def expire_reservations():
    total = 0
    ids = Reservation.objects.filter(status='active', expires_at__lte=timezone.now()).values_list('copy_id', flat=True).distinct()
    for copy_id in list(ids):
        with transaction.atomic():
            copy = Copy.objects.select_for_update().get(pk=copy_id)
            total += expire_copy_reservations(copy)
    return total


def process_notifications():
    """Run periodically. A Loan lock serializes delivery against returns/workers.

    SMTP is not transactional: a crash after SMTP acceptance but before commit
    can cause a retry duplicate. We promise recorded retries, not exactly-once SMTP.
    """
    today = timezone.localdate()
    sent = failed = 0
    ids = Loan.objects.filter(returned_at__isnull=True).values_list('pk', flat=True)
    for loan_id in list(ids):
        with transaction.atomic():
            loan = Loan.objects.select_for_update().get(pk=loan_id)
            if loan.returned_at or not loan.reader.email_verified:
                continue
            days = (loan.due_date - today).days
            if days >= 0:
                kind, period = 'reminder', loan.due_date
                planned_date = loan.due_date - timedelta(days=settings.REMINDER_DAYS)
            elif days < 0:
                bucket = ((-days - 1) // settings.OVERDUE_REPEAT_DAYS) * settings.OVERDUE_REPEAT_DAYS + 1
                kind, period = 'overdue', loan.due_date + timedelta(days=bucket)
                planned_date = period
            scheduled_at = max(loan.issued_at, timezone.make_aware(datetime.combine(planned_date, time.min)))
            # Old failed reminders must not be retried after their time window.
            loan.notifications.filter(state__in=['pending', 'failed']).exclude(kind=kind, period=period).update(state='cancelled')
            message, _ = Notification.objects.get_or_create(
                loan=loan, kind=kind, period=period,
                defaults={'recipient': loan.reader.email, 'scheduled_at': scheduled_at},
            )
            if message.state in ('sent', 'cancelled'):
                continue
            if message.scheduled_at != scheduled_at:
                message.scheduled_at = scheduled_at
                message.save(update_fields=['scheduled_at'])
            if message.scheduled_at > timezone.now():
                continue
            message.attempts += 1
            title = 'Час повернути книгу' if kind == 'reminder' else 'Книгу не повернуто вчасно'
            body = f'{loan.copy.book.title}\nПовернути до: {loan.due_date:%d.%m.%Y}\nВаш кабінет: {settings.SITE_URL}{reverse("account")}\n'
            try:
                count = send_mail(title, body, settings.DEFAULT_FROM_EMAIL, [message.recipient])
                if count != 1:
                    raise RuntimeError('Mail backend did not accept the message')
                message.state, message.last_error, message.sent_at = 'sent', '', timezone.now()
                sent += 1
            except Exception as exc:
                message.state = 'failed'
                # Store error type, not credentials potentially echoed by a provider.
                message.last_error = type(exc).__name__
                logger.error('Loan notification %s failed: %s', message.pk, type(exc).__name__)
                failed += 1
            message.save()
    return sent, failed
