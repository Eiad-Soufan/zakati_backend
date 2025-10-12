from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.core.validators import MinLengthValidator

# ========= Profile (مطابق للواجهة) =========
class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=120)
    phone_number = models.CharField(max_length=32, validators=[MinLengthValidator(5)])
    country = models.CharField(max_length=80)
    city = models.CharField(max_length=80)
    avatar_url = models.URLField(blank=True, default="")

    # عدّاد نسخة اللقطة (للتزامن)
    snapshot_version = models.PositiveBigIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    def is_complete(self):
        return all([
            bool(self.full_name.strip()),
            bool(self.phone_number.strip()),
            bool(self.country.strip()),
            bool(self.city.strip()),
        ])

    def __str__(self):
        return f"Profile<{self.user.username}>"

# إنشاء بروفايل تلقائيًا عند إنشاء المستخدم
@receiver(post_save, sender=User)
def create_profile_for_user(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

# ========= إعدادات المستخدم (تظهر في شاشة الإعدادات) =========
class UserSettings(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    display_currency = models.CharField(max_length=3, default="USD")  # عملة العرض
    user_fx_overrides = models.JSONField(default=dict, blank=True)    # { "USD->SYP": 13500.0 }

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Settings<{self.user.username}>"

@receiver(post_save, sender=User)
def create_settings_for_user(sender, instance, created, **kwargs):
    if created:
        UserSettings.objects.create(user=instance)

# ========= إشعارات داخل التطبيق =========
class Notification(models.Model):
    TYPE_CHOICES = (
        ("ZAKAT_REMINDER", "ZAKAT_REMINDER"),
        ("ANNOUNCEMENT", "ANNOUNCEMENT"),
    )
    PRIORITY_CHOICES = (
        ("normal", "normal"),
        ("important", "important"),
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=280, blank=True, default="")
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="normal")
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)
    meta_key = models.CharField(max_length=120, blank=True, default="")
    class Meta:
        ordering = ["-created_at"]

    def mark_read(self):
        if not self.read_at:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at"])

from django.utils import timezone

# === أسعار المعادن العامة (لكل غرام) ===
class MetalPrice(models.Model):
    METAL_CHOICES = (("GOLD", "GOLD"), ("SILVER", "SILVER"))
    metal = models.CharField(max_length=10, choices=METAL_CHOICES)
    price_per_gram = models.DecimalField(max_digits=18, decimal_places=6)  # عادة بالدولار
    currency = models.CharField(max_length=3, default="USD")
    source = models.CharField(max_length=50, default="manual")  # manual/provider-name
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["metal", "-fetched_at"])]
        ordering = ["-fetched_at"]

# === أسعار الصرف العامة ===
class FxRate(models.Model):
    base = models.CharField(max_length=3)   # مثال: USD
    quote = models.CharField(max_length=3)  # مثال: SYP
    rate = models.DecimalField(max_digits=24, decimal_places=10)
    source = models.CharField(max_length=50, default="manual")
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["base", "quote", "-fetched_at"])]
        ordering = ["-fetched_at"]

from decimal import Decimal
from django.core.validators import MinValueValidator
from django.utils import timezone

ASSET_TYPES = (
    ("GOLD", "GOLD"),
    ("SILVER", "SILVER"),
    ("CASH", "CASH"),
)
OP_TYPES = (
    ("ADD", "ADD"),
    ("WITHDRAW", "WITHDRAW"),
    ("ZAKAT", "ZAKAT"),
)

class Transaction(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions")

    asset_type = models.CharField(max_length=6, choices=ASSET_TYPES)
    operation_type = models.CharField(max_length=8, choices=OP_TYPES)

    # للذهب فقط
    karat = models.PositiveSmallIntegerField(null=True, blank=True)  # 18/21/24
    weight_g = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True,
                                   validators=[MinValueValidator(Decimal("0.000001"))])

    # للأموال فقط
    currency_code = models.CharField(max_length=3, blank=True, default="")
    amount = models.DecimalField(max_digits=24, decimal_places=10, null=True, blank=True,
                                 validators=[MinValueValidator(Decimal("0.0000000001"))])

    # مشتركة
    date = models.DateField(default=timezone.now)
    notes = models.CharField(max_length=280, blank=True, default="")
    invoice_image_url = models.URLField(blank=True, default="")

    # التدقيق/التعديل
    previous_version = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="revisions")
    is_edited = models.BooleanField(default=False)
    edit_reason = models.CharField(max_length=180, blank=True, default="")
    soft_deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "asset_type"]),
            models.Index(fields=["user", "asset_type", "karat"]),
            models.Index(fields=["user", "asset_type", "currency_code"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def is_active(self):
        return self.soft_deleted_at is None

    def clean(self):
        # تحقق أساسي من ملء الحقول حسب نوع الأصل
        if self.asset_type == "GOLD":
            if self.weight_g is None or self.weight_g <= 0:
                raise ValueError("weight_g مطلوب للذهب.")
            if self.karat not in (18, 21, 24):
                raise ValueError("karat للذهب يجب أن يكون 18 أو 21 أو 24.")
        elif self.asset_type == "SILVER":
            if self.weight_g is None or self.weight_g <= 0:
                raise ValueError("weight_g مطلوب للفضة.")
            self.karat = None
        elif self.asset_type == "CASH":
            if not self.currency_code or self.amount is None or self.amount <= 0:
                raise ValueError("currency_code و amount مطلوبان للأموال.")
            self.karat = None
            self.weight_g = None

    def __str__(self):
        return f"Tx<{self.user_id} {self.asset_type} {self.operation_type}>"

class ZakatAnchor(models.Model):
    GROUP_CHOICES = (
        ("GOLD_PURE", "GOLD_PURE"),
        ("SILVER", "SILVER"),
        ("CASH_POOL", "CASH_POOL"),
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="zakat_anchors")
    asset_group = models.CharField(max_length=16, choices=GROUP_CHOICES)
    start_hijri_year = models.IntegerField(null=True, blank=True)   # 1447 مثلاً
    start_hijri_month = models.IntegerField(null=True, blank=True)  # 1..12
    start_hijri_day = models.IntegerField(null=True, blank=True)    # 1..30
    due_hijri_year = models.IntegerField(null=True, blank=True)
    due_hijri_month = models.IntegerField(null=True, blank=True)
    due_hijri_day = models.IntegerField(null=True, blank=True)

    status = models.CharField(max_length=20, default="ACTIVE")  # ACTIVE / RESET
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "asset_group")]
        indexes = [models.Index(fields=["user", "asset_group"])]
