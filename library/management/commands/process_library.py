from django.core.management.base import BaseCommand
from library.services import expire_reservations, process_notifications

class Command(BaseCommand):
    help = 'Expire unused reservations and send due-date notifications; schedule every 5 minutes.'
    def handle(self, *args, **options):
        expired = expire_reservations()
        sent, failed = process_notifications()
        self.stdout.write(f'Expired: {expired}; sent: {sent}; failed: {failed}')
