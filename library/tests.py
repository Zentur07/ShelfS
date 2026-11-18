from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import services
from .forms import SignupForm
from .models import Author, Book, Copy, Genre, Loan, Notification, Reservation, Review, User


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class LibraryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = User.objects.create_user('reader@example.com', 'ShelfS-test-password-61', first_name='Олена', email_verified=True)
        cls.other = User.objects.create_user('other@example.com', 'ShelfS-test-password-62', email_verified=True)
        cls.staff = User.objects.create_user('staff@example.com', 'ShelfS-test-password-63', is_staff=True)
        cls.book = Book.objects.create(title='Лісова пісня', description='Тестовий опис')
        cls.author = Author.objects.create(name='Леся Українка')
        cls.genre = Genre.objects.create(name='Драма')
        cls.book.authors.add(cls.author)
        cls.book.genres.add(cls.genre)
        cls.copy = Copy.objects.create(book=cls.book, inventory_number='TEST-001')

    def loan(self, **kwargs):
        return Loan.objects.create(reader=self.reader, copy=self.copy, due_date=kwargs.pop('due_date', timezone.localdate() + timedelta(days=2)), **kwargs)

    def test_reservation_requires_confirmed_email(self):
        self.reader.email_verified = False
        self.reader.save()
        with self.assertRaises(ValidationError):
            services.reserve(self.reader, self.book.pk)
        self.assertFalse(Reservation.objects.exists())

    def test_blocked_reader_can_login_and_view_but_cannot_reserve(self):
        services.set_restriction(self.staff, self.reader.pk, True, 'Неповернена книга')
        self.assertTrue(self.client.login(email=self.reader.email, password='ShelfS-test-password-61'))
        self.assertEqual(self.client.get(reverse('account')).status_code, 200)
        with self.assertRaises(ValidationError):
            services.reserve(self.reader, self.book.pk)

    def test_unblocking_restores_reservation(self):
        services.set_restriction(self.staff, self.reader.pk, True, 'Причина')
        services.set_restriction(self.staff, self.reader.pk, False)
        self.assertEqual(services.reserve(self.reader, self.book.pk).reader, self.reader)

    def test_block_requires_reason(self):
        with self.assertRaises(ValidationError):
            services.set_restriction(self.staff, self.reader.pk, True, ' ')

    def test_repeated_reservation_returns_same_record(self):
        first = services.reserve(self.reader, self.book.pk)
        second = services.reserve(self.reader, self.book.pk)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_other_reader_cannot_reserve_occupied_copy(self):
        services.reserve(self.reader, self.book.pk)
        with self.assertRaises(ValidationError):
            services.reserve(self.other, self.book.pk)

    def test_cannot_cancel_another_readers_reservation(self):
        record = services.reserve(self.reader, self.book.pk)
        with self.assertRaises(PermissionDenied):
            services.cancel_reservation(self.other, record.pk)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse('cancel', args=[record.pk])).status_code, 404)

    def test_cancellation_frees_copy_and_preserves_history(self):
        record = services.reserve(self.reader, self.book.pk)
        services.cancel_reservation(self.reader, record.pk)
        record.refresh_from_db()
        self.assertEqual(record.status, 'cancelled')
        self.assertEqual(services.available_copies().count(), 1)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_expired_reservation_freed_even_before_scheduler(self):
        now = timezone.now()
        old = Reservation.objects.create(reader=self.reader, copy=self.copy, created_at=now-timedelta(days=3), expires_at=now-timedelta(days=1))
        new = services.reserve(self.other, self.book.pk)
        old.refresh_from_db()
        self.assertEqual(old.status, 'expired')
        self.assertEqual(new.reader, self.other)

    def test_expiry_task_is_idempotent(self):
        now = timezone.now()
        Reservation.objects.create(reader=self.reader, copy=self.copy, created_at=now-timedelta(days=2), expires_at=now-timedelta(days=1))
        self.assertEqual(services.expire_reservations(), 1)
        self.assertEqual(services.expire_reservations(), 0)

    def test_full_circulation_flow(self):
        reservation = services.reserve(self.reader, self.book.pk)
        loan = services.issue_loan(self.staff, self.reader.pk, self.copy.pk, timezone.localdate()+timedelta(days=14))
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, 'fulfilled')
        self.assertEqual(services.available_copies().count(), 0)
        services.return_loan(self.staff, loan.pk)
        loan.refresh_from_db()
        self.assertIsNotNone(loan.returned_at)
        self.assertEqual(services.available_copies().count(), 1)
        self.assertEqual(Loan.objects.count(), 1)

    def test_reader_cannot_issue_or_return(self):
        with self.assertRaises(PermissionDenied):
            services.issue_loan(self.reader, self.reader.pk, self.copy.pk, timezone.localdate())
        loan = self.loan()
        with self.assertRaises(PermissionDenied):
            services.return_loan(self.reader, loan.pk)

    def test_reserved_copy_cannot_be_issued_to_another_reader(self):
        services.reserve(self.reader, self.book.pk)
        with self.assertRaises(ValidationError):
            services.issue_loan(self.staff, self.other.pk, self.copy.pk, timezone.localdate())

    def test_duplicate_issue_is_rejected(self):
        self.loan()
        with self.assertRaises(ValidationError):
            services.issue_loan(self.staff, self.reader.pk, self.copy.pk, timezone.localdate())

    def test_return_is_idempotent(self):
        loan = self.loan()
        first = services.return_loan(self.staff, loan.pk)
        second = services.return_loan(self.staff, loan.pk)
        self.assertEqual(first.returned_at, second.returned_at)

    def test_cannot_issue_inactive_copy_or_backdated_loan(self):
        with self.assertRaises(ValidationError):
            services.issue_loan(self.staff, self.reader.pk, self.copy.pk, timezone.localdate()-timedelta(days=1))
        self.copy.is_active = False
        self.copy.save()
        with self.assertRaises(ValidationError):
            services.issue_loan(self.staff, self.reader.pk, self.copy.pk, timezone.localdate())

    def test_database_rejects_duplicate_active_reservation(self):
        services.reserve(self.reader, self.book.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reservation.objects.create(reader=self.other, copy=self.copy, expires_at=timezone.now()+timedelta(days=1))

    def test_database_rejects_duplicate_open_loan(self):
        self.loan()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.loan()

    def test_history_protects_copy_and_book_from_deletion(self):
        self.loan()
        with self.assertRaises(ProtectedError):
            self.copy.delete()
        with self.assertRaises(ProtectedError):
            self.book.delete()

    def test_account_does_not_expose_other_readers_records(self):
        record = services.reserve(self.other, self.book.pk)
        self.client.force_login(self.reader)
        response = self.client.get(reverse('account'))
        self.assertNotIn(record, response.context['reservations'])

    def test_staff_routes_reject_reader(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(reverse('staff')).status_code, 403)
        self.assertEqual(self.client.post(reverse('return', args=[self.loan().pk])).status_code, 403)

    def test_state_changes_require_post_and_csrf(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(reverse('reserve', args=[self.book.pk])).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.reader)
        self.assertEqual(csrf_client.post(reverse('reserve', args=[self.book.pk])).status_code, 403)

    def test_pages_render_and_search_filters_work(self):
        for url in ['/', reverse('book', args=[self.book.pk]), '/login/', '/signup/']:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get('/', {'q': 'Лісова'}), self.book.title)
        self.assertContains(self.client.get('/', {'q': 'Українка'}), self.book.title)
        self.assertContains(self.client.get('/', {'genre': self.genre.pk}), self.book.title)
        self.assertNotContains(self.client.get('/', {'q': 'missing'}), self.book.title)
        services.reserve(self.reader, self.book.pk)
        self.assertNotContains(self.client.get('/', {'available': '1'}), self.book.title)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/staff/').status_code, 200)

    def test_signup_hashes_password_and_cannot_create_staff(self):
        response = self.client.post('/signup/', {
            'first_name': 'Іван', 'last_name': 'Петренко', 'patronymic': 'Іванович',
            'email': 'NEW@EXAMPLE.COM', 'password1': 'Long-test-password-732',
            'password2': 'Long-test-password-732', 'is_staff': 'on', 'email_verified': 'on',
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='new@example.com')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.email_verified)
        self.assertTrue(user.check_password('Long-test-password-732'))
        self.assertNotEqual(user.password, 'Long-test-password-732')
        self.assertEqual(len(mail.outbox), 1)

    def test_duplicate_email_is_case_insensitive(self):
        form = SignupForm(data={'first_name': 'Іван', 'last_name': 'Петренко', 'patronymic': 'Іванович', 'email': 'READER@EXAMPLE.COM', 'password1': 'Long-test-password-732', 'password2': 'Long-test-password-732'})
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_confirmation_tampering_expiration_and_repeat(self):
        self.reader.email_verified = False
        self.reader.save()
        token = services.confirmation_token(self.reader)
        with self.assertRaises(ValidationError):
            services.confirm_email(token + 'x')
        with override_settings(CONFIRMATION_MAX_AGE=-1), self.assertRaises(ValidationError):
            services.confirm_email(token)
        services.confirm_email(token)
        services.confirm_email(token)
        self.reader.refresh_from_db()
        self.assertTrue(self.reader.email_verified)
        self.assertEqual(User.objects.count(), 3)

    def test_confirmation_resend_has_cooldown(self):
        self.reader.email_verified = False
        self.reader.save()
        self.assertTrue(services.send_confirmation(self.reader))
        with self.assertRaises(ValidationError):
            services.send_confirmation(self.reader)

    @patch('library.services.send_mail', side_effect=OSError('offline'))
    def test_signup_survives_email_failure(self, mocked):
        self.client.post('/signup/', {'first_name': 'Іван', 'last_name': 'Петренко', 'patronymic': 'Іванович', 'email': 'new@example.com', 'password1': 'Long-test-password-732', 'password2': 'Long-test-password-732'})
        self.assertTrue(User.objects.filter(email='new@example.com', email_verified=False).exists())

    def test_review_rejects_empty_and_escapes_html(self):
        self.client.force_login(self.reader)
        self.client.post(reverse('review', args=[self.book.pk]), {'text': '  '})
        self.assertFalse(Review.objects.exists())
        self.client.post(reverse('review', args=[self.book.pk]), {'text': '<script>alert(1)</script>'})
        response = self.client.get(reverse('book', args=[self.book.pk]))
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_notifications_sent_once_to_correct_reader(self):
        self.loan()
        self.assertEqual(services.process_notifications(), (1, 0))
        self.assertEqual(services.process_notifications(), (0, 0))
        self.assertEqual(mail.outbox[0].to, [self.reader.email])
        self.assertIn(self.book.title, mail.outbox[0].body)

    def test_no_notifications_after_return(self):
        loan = self.loan()
        services.return_loan(self.staff, loan.pk)
        self.assertEqual(services.process_notifications(), (0, 0))

    def test_unverified_address_not_notified(self):
        self.reader.email_verified = False
        self.reader.save()
        self.loan()
        self.assertEqual(services.process_notifications(), (0, 0))

    def test_overdue_notification_period(self):
        self.loan(due_date=timezone.localdate()-timedelta(days=4))
        services.process_notifications()
        notification = Notification.objects.get()
        self.assertEqual(notification.kind, 'overdue')
        self.assertEqual(notification.period, timezone.localdate())

    def test_email_failure_can_retry_without_changing_loan(self):
        loan = self.loan()
        with patch('library.services.send_mail', side_effect=OSError('offline')):
            self.assertEqual(services.process_notifications(), (0, 1))
        self.assertEqual(Notification.objects.get().state, 'failed')
        loan.refresh_from_db()
        self.assertIsNone(loan.returned_at)
        self.assertEqual(services.process_notifications(), (1, 0))
        self.assertEqual(Notification.objects.get().attempts, 2)

    def test_return_cancels_failed_notification(self):
        loan = self.loan()
        with patch('library.services.send_mail', side_effect=OSError('offline')):
            services.process_notifications()
        services.return_loan(self.staff, loan.pk)
        self.assertEqual(Notification.objects.get().state, 'cancelled')
        self.assertEqual(services.process_notifications(), (0, 0))

    def test_librarian_admin_cannot_edit_loans_or_grant_privileges(self):
        group = Group.objects.create(name='Test librarians')
        group.permissions.set(Permission.objects.filter(content_type__app_label='library'))
        self.staff.groups.add(group)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse('admin:library_user_change', args=[self.reader.pk]), {'is_staff': True}).status_code, 403)
        self.assertEqual(self.client.post(reverse('admin:library_loan_add'), {}).status_code, 403)


@skipUnless(connection.vendor == 'postgresql', 'Real row-lock concurrency requires PostgreSQL, not SQLite')
class PostgreSQLConcurrencyTests(TransactionTestCase):
    def test_two_readers_compete_for_last_copy(self):
        book = Book.objects.create(title='Останній примірник')
        Copy.objects.create(book=book, inventory_number='PG-001')
        users = [User.objects.create_user(f'pg{i}@example.com', email_verified=True) for i in range(2)]
        barrier = Barrier(2)
        def attempt(user_id):
            close_old_connections()
            try:
                reader = User.objects.get(pk=user_id)
                barrier.wait(timeout=10)
                try:
                    services.reserve(reader, book.pk)
                    return 'ok'
                except ValidationError:
                    return 'unavailable'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, [user.pk for user in users]))
        self.assertCountEqual(results, ['ok', 'unavailable'])
        self.assertEqual(Reservation.objects.filter(status='active').count(), 1)
