# api/management/commands/sync_zakat.py
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from api.utils import update_zakat_anchors_and_reminders

User = get_user_model()

class Command(BaseCommand):
    help = "تحديث نقاط الحَول وتوليد تذكيرات الزكاة لجميع المستخدمين"

    def handle(self, *args, **options):
        for user in User.objects.all():
            try:
                update_zakat_anchors_and_reminders(user)
            except Exception as e:
                self.stderr.write(f"Failed for user {user.id}: {e}")
        self.stdout.write(self.style.SUCCESS("تم تحديث تذكيرات الزكاة."))
