# api/management/commands/fetch_rates.py
from django.core.management.base import BaseCommand
from api.providers import fetch_and_store_rates

class Command(BaseCommand):
    help = "جلب أسعار الصرف والمعادن من المزوّدات وتخزينها"

    def handle(self, *args, **options):
        try:
            fetch_and_store_rates()
            self.stdout.write(self.style.SUCCESS("تم جلب الأسعار وتخزينها بنجاح."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"فشل جلب الأسعار: {e}"))
            raise
