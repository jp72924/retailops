from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Payment, SystemSettings


class Command(BaseCommand):
    help = 'Delete stored receipt images older than the configured retention period.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            help='Override SystemSettings.delete_receipt_image_after_days for this run.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show how many images would be purged without deleting files.',
        )

    def handle(self, *args, **options):
        days = options.get('days')
        if days is None:
            days = SystemSettings.get().delete_receipt_image_after_days
        if days <= 0:
            raise CommandError('Retention days must be greater than zero.')

        cutoff = timezone.now() - timedelta(days=days)
        qs = (
            Payment.objects
            .exclude(receipt_image='')
            .filter(receipt_image__isnull=False, created_at__lt=cutoff)
            .order_by('created_at')
        )
        total = qs.count()

        if options.get('dry_run'):
            self.stdout.write(
                self.style.WARNING(
                    f'{total} receipt image(s) older than {days} day(s) would be purged.'
                )
            )
            return

        purged = 0
        for payment in qs.iterator():
            if not payment.receipt_image:
                continue
            payment.receipt_image.delete(save=False)
            payment.receipt_image = None
            payment.save(update_fields=['receipt_image'])
            purged += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Purged {purged} receipt image(s) older than {days} day(s).'
            )
        )
