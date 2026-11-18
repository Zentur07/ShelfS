from datetime import timedelta
from unittest.mock import patch
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from . import services
from .models import Book, Copy, Loan, Notification, Reservation, Review, User


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = User.objects.create_user('accept@example.test', email_verified=True)
        cls.other = User.objects.create_user('private@example.test', email_verified=True)
        cls.staff = User.objects.create_user('librarian@example.test', is_staff=True)
        cls.book = Book.objects.create(title='ЛІСОВА ПІСНЯ')
        cls.copy = Copy.objects.create(book=cls.book, inventory_number='ACCEPT-001')

    def test_cancel_requires_explicit_confirmation_and_csrf(self):
        record = services.reserve(self.reader, self.book.pk)
        self.client.force_login(self.reader)
        url = reverse('cancel', args=[record.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 400)
        record.refresh_from_db()
        self.assertEqual(record.status, 'active')
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.reader)
        self.assertEqual(csrf_client.post(url, {'confirmed': 'yes'}).status_code, 403)
        self.assertEqual(self.client.post(url, {'confirmed': 'yes'}).status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, 'cancelled')

    def test_all_old_records_remain_accessible_and_private(self):
        now = timezone.now()
        Reservation.objects.bulk_create([Reservation(reader=self.reader, copy=self.copy,
            status='cancelled', expires_at=now+timedelta(days=1)) for _ in range(105)])
        Loan.objects.bulk_create([Loan(reader=self.reader, copy=self.copy,
            due_date=timezone.localdate(), returned_at=now) for _ in range(105)])
        Loan.objects.create(reader=self.other, copy=self.copy, due_date=timezone.localdate(), returned_at=now)
        current = Loan.objects.create(reader=self.reader, copy=self.copy, due_date=timezone.localdate())
        self.client.force_login(self.reader)
        seen = set()
        for page in range(1, 7):
            response = self.client.get('/account/', {'loan_state': 'history', 'loans_page': page, 'reservations_page': page})
            self.assertEqual(response.status_code, 200)
            seen.update(row.pk for row in response.context['loans'])
            self.assertEqual(response.context['reservations'].paginator.count, 105)
            self.assertTrue(all(row.reader_id == self.reader.pk for row in response.context['loans']))
        self.assertEqual(len(seen), 105)
        response = self.client.get('/account/', {'loan_state': 'current'})
        self.assertEqual(list(response.context['loans']), [current])

    def test_reviews_beyond_old_limit_can_be_read(self):
        Review.objects.bulk_create([Review(reader=self.reader, book=self.book, text=f'Відгук {i}') for i in range(55)])
        response = self.client.get(reverse('book', args=[self.book.pk]), {'reviews_page': 3})
        self.assertEqual(response.context['reviews'].paginator.count, 55)
        self.assertEqual(len(response.context['reviews']), 15)

    def test_future_notification_is_planned_then_cancelled_on_return(self):
        loan = Loan.objects.create(reader=self.reader, copy=self.copy, due_date=timezone.localdate()+timedelta(days=14))
        self.assertEqual(services.process_notifications(), (0, 0))
        notice = Notification.objects.get(loan=loan)
        self.assertEqual(notice.state, 'pending')
        self.assertGreater(notice.scheduled_at, timezone.now())
        self.assertEqual(notice.attempts, 0)
        services.return_loan(self.staff, loan.pk)
        notice.refresh_from_db()
        self.assertEqual(notice.state, 'cancelled')
        self.assertEqual(services.process_notifications(), (0, 0))

    def test_future_notification_is_sent_when_due(self):
        today = timezone.localdate()
        loan = Loan.objects.create(reader=self.reader, copy=self.copy, due_date=today+timedelta(days=14))
        services.process_notifications()
        future = timezone.now()+timedelta(days=12)
        with patch('library.services.timezone.localdate', return_value=today+timedelta(days=12)), patch('library.services.timezone.now', return_value=future):
            self.assertEqual(services.process_notifications(), (1, 0))
            self.assertEqual(services.process_notifications(), (0, 0))
        self.assertEqual(Notification.objects.get(loan=loan).state, 'sent')
        self.assertEqual(mail.outbox[0].to, [self.reader.email])

    def test_resend_revokes_old_link_but_delivery_failure_preserves_it(self):
        self.reader.email_verified = False
        self.reader.save()
        services.send_confirmation(self.reader)
        self.reader.refresh_from_db()
        first = services.confirmation_token(self.reader)
        User.objects.filter(pk=self.reader.pk).update(confirmation_sent_at=timezone.now()-timedelta(minutes=2))
        with patch('library.services.send_mail', return_value=0):
            self.assertFalse(services.send_confirmation(self.reader))
        self.reader.refresh_from_db()
        first_nonce = self.reader.confirmation_nonce
        self.assertTrue(services.send_confirmation(self.reader))
        self.reader.refresh_from_db()
        self.assertNotEqual(first_nonce, self.reader.confirmation_nonce)
        with self.assertRaises(ValidationError):
            services.confirm_email(first)
        services.confirm_email(services.confirmation_token(self.reader))
        self.reader.refresh_from_db()
        self.assertTrue(self.reader.email_verified)

    def test_failed_issue_rolls_back_loan_and_reservation(self):
        record = services.reserve(self.reader, self.book.pk)
        with patch('library.models.Reservation.save', side_effect=RuntimeError('simulated write failure')):
            with self.assertRaises(RuntimeError):
                services.issue_loan(self.staff, self.reader.pk, self.copy.pk, timezone.localdate())
        self.assertFalse(Loan.objects.exists())
        record.refresh_from_db()
        self.assertEqual(record.status, 'active')

    def test_superuser_can_create_user_without_username(self):
        admin = User.objects.create_superuser('root@example.test', 'Valid-local-password-918')
        self.client.force_login(admin)
        url = reverse('admin:library_user_add')
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {'email': 'added@example.test', 'first_name': 'Іван', 'last_name': 'Тест',
            'password1': 'Valid-local-password-917', 'password2': 'Valid-local-password-917', '_save': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.get(email='added@example.test').check_password('Valid-local-password-917'))

    def test_cyrillic_search_is_case_insensitive_on_postgresql(self):
        from django.db import connection
        if connection.vendor != 'postgresql':
            self.skipTest('Ukrainian case folding is verified on the target PostgreSQL database')
        self.assertContains(self.client.get('/', {'q': 'лісова пісня'}), self.book.title)
