from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers
from .models import Profile, UserSettings, Notification

User = get_user_model()

class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=6, write_only=True)

    def create(self, validated):
        email = validated["email"].strip().lower()
        password = validated["password"]
        username = email  # نبسّطها: اسم المستخدم = البريد
        user = User.objects.create_user(username=username, email=email, password=password)
        return user

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs.get("email", "").strip().lower()
        password = attrs.get("password")
        user = authenticate(username=email, password=password)
        if not user:
            raise serializers.ValidationError("بيانات الدخول غير صحيحة.")
        attrs["user"] = user
        return attrs

from rest_framework import serializers
from .models import Profile

class ProfileSerializer(serializers.ModelSerializer):
    is_complete = serializers.SerializerMethodField(read_only=True)
    avatar_base64 = serializers.CharField(write_only=True, required=False, allow_blank=True)
    avatar_clear = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = Profile
        fields = [
            "full_name", "phone_number", "country", "city",
            "avatar_url", "is_complete",
            "avatar_base64", "avatar_clear",
        ]
        read_only_fields = ["avatar_url", "is_complete"]

    def get_is_complete(self, obj):
        # تستدعي دالة الموديل كما كتبناها سابقاً
        return obj.is_complete()

    def update(self, instance, validated_data):
        # ... نفس كود التحديث الذي وضعناه سابقًا (avatar_clear / avatar_base64 / الحقول النصية) ...
        for f in ["full_name", "phone_number", "country", "city"]:
            if f in validated_data:
                setattr(instance, f, validated_data[f])

        if validated_data.get("avatar_clear"):
            instance.avatar_url = ""

        avatar_b64 = validated_data.get("avatar_base64", "").strip()
        if avatar_b64:
            from .utils import save_base64_image_to_media
            try:
                url = save_base64_image_to_media(avatar_b64, subdir="avatars")
            except ValueError as e:
                code = str(e)
                msg_map = {
                    "empty_base64": "صورة غير صالحة.",
                    "invalid_base64": "صيغة Base64 غير صحيحة.",
                    "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                    "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
                }
                raise serializers.ValidationError({"avatar_base64": msg_map.get(code, "فشل رفع الصورة.")})
            instance.avatar_url = url

        instance.save()
        return instance


class UserSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSettings
        fields = ["display_currency", "user_fx_overrides"]

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "type", "title", "body", "priority", "created_at", "read_at"]

# ========== Snapshot ==========
class BootstrapSnapshotSerializer(serializers.Serializer):
    version = serializers.IntegerField()
    etag = serializers.CharField()
    generated_at = serializers.DateTimeField()
    profile = ProfileSerializer()
    settings = UserSettingsSerializer()
    # ستضاف لاحقًا: assets, transactions, rates, zakat_cycles، ... (الآن نتركها فارغة)
    notifications = NotificationSerializer(many=True)


from .models import MetalPrice, FxRate

class RatesResponseSerializer(serializers.Serializer):
    # مبسّط: نعيد أحدث سعر ذهب/فضة (لكل غرام) وعملة العرض ومجموعة من أزواج الصرف
    display_currency = serializers.CharField()
    metals = serializers.DictField()  # {"gold_g_per": "...", "silver_g_per": "...", "currency": "USD", "fetched_at": "..."}
    fx = serializers.ListField()      # [{"base":"USD","quote":"SYP","rate":"...","fetched_at":"..."}]
    user_overrides = serializers.DictField()  # نفس ما خزّنه المستخدم
    etag = serializers.CharField()

from decimal import Decimal
from rest_framework import serializers
from .models import Transaction

class CashAddSerializer(serializers.Serializer):
    currency_code = serializers.CharField(max_length=3)
    amount = serializers.DecimalField(max_digits=24, decimal_places=10)
    date = serializers.DateField(required=False)
    notes = serializers.CharField(max_length=280, required=False, allow_blank=True)
    invoice_base64 = serializers.CharField(required=False, allow_blank=True)

    def validate_currency_code(self, v):
        v = v.strip().upper()
        if len(v) != 3:
            raise serializers.ValidationError("رمز العملة يجب أن يكون 3 أحرف (مثال USD).")
        return v

    def validate_amount(self, v):
        if v <= Decimal("0"):
            raise serializers.ValidationError("المبلغ يجب أن يكون أكبر من الصفر.")
        return v


from decimal import Decimal
from rest_framework import serializers

class CashWithdrawSerializer(serializers.Serializer):
    currency_code = serializers.CharField(max_length=3)
    amount = serializers.DecimalField(max_digits=24, decimal_places=10)
    date = serializers.DateField(required=False)
    notes = serializers.CharField(max_length=280, required=False, allow_blank=True)
    invoice_base64 = serializers.CharField(required=False, allow_blank=True)

    def validate_currency_code(self, v):
        v = v.strip().upper()
        if len(v) != 3:
            raise serializers.ValidationError("رمز العملة يجب أن يكون 3 أحرف (مثال USD).")
        return v

    def validate_amount(self, v):
        if v <= Decimal("0"):
            raise serializers.ValidationError("المبلغ يجب أن يكون أكبر من الصفر.")
        return v


class CashZakatSerializer(serializers.Serializer):
    currency_code = serializers.CharField(max_length=3)
    amount = serializers.DecimalField(max_digits=24, decimal_places=10)
    date = serializers.DateField(required=False)
    notes = serializers.CharField(max_length=280, required=False, allow_blank=True)
    invoice_base64 = serializers.CharField(required=False, allow_blank=True)

    def validate_currency_code(self, v):
        v = v.strip().upper()
        if len(v) != 3:
            raise serializers.ValidationError("رمز العملة يجب أن يكون 3 أحرف (مثال USD).")
        return v

    def validate_amount(self, v):
        if v <= Decimal("0"):
            raise serializers.ValidationError("المبلغ يجب أن يكون أكبر من الصفر.")
        return v

from rest_framework import serializers
from decimal import Decimal

class GoldAddSerializer(serializers.Serializer):
    karat = serializers.IntegerField()
    weight_g = serializers.DecimalField(max_digits=18, decimal_places=6)
    date = serializers.DateField(required=False)
    notes = serializers.CharField(max_length=280, required=False, allow_blank=True)
    invoice_base64 = serializers.CharField(required=False, allow_blank=True)

    def validate_karat(self, v):
        if v not in (18, 21, 24):
            raise serializers.ValidationError("العيار يجب أن يكون 18 أو 21 أو 24.")
        return v

    def validate_weight_g(self, v):
        if v <= Decimal("0"):
            raise serializers.ValidationError("الوزن بالجرام يجب أن يكون أكبر من الصفر.")
        return v


class GoldWithdrawSerializer(serializers.Serializer):
    karat = serializers.IntegerField()
    weight_g = serializers.DecimalField(max_digits=18, decimal_places=6)
    date = serializers.DateField(required=False)
    notes = serializers.CharField(max_length=280, required=False, allow_blank=True)
    invoice_base64 = serializers.CharField(required=False, allow_blank=True)

    def validate_karat(self, v):
        if v not in (18, 21, 24):
            raise serializers.ValidationError("العيار يجب أن يكون 18 أو 21 أو 24.")
        return v

    def validate_weight_g(self, v):
        if v <= Decimal("0"):
            raise serializers.ValidationError("الوزن بالجرام يجب أن يكون أكبر من الصفر.")
        return v


class GoldZakatSerializer(serializers.Serializer):
    karat = serializers.IntegerField()
    weight_g = serializers.DecimalField(max_digits=18, decimal_places=6)
    date = serializers.DateField(required=False)
    notes = serializers.CharField(max_length=280, required=False, allow_blank=True)
    invoice_base64 = serializers.CharField(required=False, allow_blank=True)

    def validate_karat(self, v):
        if v not in (18, 21, 24):
            raise serializers.ValidationError("العيار يجب أن يكون 18 أو 21 أو 24.")
        return v

    def validate_weight_g(self, v):
        if v <= Decimal("0"):
            raise serializers.ValidationError("الوزن بالجرام يجب أن يكون أكبر من الصفر.")
        return v

from rest_framework import serializers
from decimal import Decimal

class SilverAddSerializer(serializers.Serializer):
    weight_g = serializers.DecimalField(max_digits=18, decimal_places=6)
    date = serializers.DateField(required=False)
    notes = serializers.CharField(max_length=280, required=False, allow_blank=True)
    invoice_base64 = serializers.CharField(required=False, allow_blank=True)

    def validate_weight_g(self, v):
        if v <= Decimal("0"):
            raise serializers.ValidationError("الوزن بالجرام يجب أن يكون أكبر من الصفر.")
        return v


class SilverWithdrawSerializer(serializers.Serializer):
    weight_g = serializers.DecimalField(max_digits=18, decimal_places=6)
    date = serializers.DateField(required=False)
    notes = serializers.CharField(max_length=280, required=False, allow_blank=True)
    invoice_base64 = serializers.CharField(required=False, allow_blank=True)

    def validate_weight_g(self, v):
        if v <= Decimal("0"):
            raise serializers.ValidationError("الوزن بالجرام يجب أن يكون أكبر من الصفر.")
        return v


class SilverZakatSerializer(serializers.Serializer):
    weight_g = serializers.DecimalField(max_digits=18, decimal_places=6)
    date = serializers.DateField(required=False)
    notes = serializers.CharField(max_length=280, required=False, allow_blank=True)
    invoice_base64 = serializers.CharField(required=False, allow_blank=True)

    def validate_weight_g(self, v):
        if v <= Decimal("0"):
            raise serializers.ValidationError("الوزن بالجرام يجب أن يكون أكبر من الصفر.")
        return v

class TransactionEditSerializer(serializers.Serializer):
    # نسمح فقط بتعديل الحقول المتعلقة بنوع الأصل
    # ونوع العملية ضمن (ADD/WITHDRAW/ZAKAT)
    operation_type = serializers.ChoiceField(choices=("ADD", "WITHDRAW", "ZAKAT"), required=False)

    # CASH
    currency_code = serializers.CharField(max_length=3, required=False)
    amount = serializers.DecimalField(max_digits=24, decimal_places=10, required=False)

    # GOLD
    karat = serializers.IntegerField(required=False)
    weight_g = serializers.DecimalField(max_digits=18, decimal_places=6, required=False)

    # SILVER
    # (تستخدم weight_g)

    date = serializers.DateField(required=False)
    notes = serializers.CharField(max_length=280, required=False, allow_blank=True)
    invoice_base64 = serializers.CharField(required=False, allow_blank=True)

    # سبب التعديل إلزامي
    edit_reason = serializers.CharField(max_length=180)

    def validate(self, attrs):
        if "edit_reason" not in attrs or not str(attrs["edit_reason"]).strip():
            raise serializers.ValidationError({"edit_reason": "سبب التعديل مطلوب."})
        # ضبط صيغة العملة إن وُجدت
        if "currency_code" in attrs and attrs["currency_code"]:
            v = attrs["currency_code"].strip().upper()
            if len(v) != 3:
                raise serializers.ValidationError({"currency_code": "رمز العملة يجب أن يكون 3 أحرف."})
            attrs["currency_code"] = v
        # تحقق أساسي للعيار إن وُجد
        if "karat" in attrs and attrs["karat"] is not None:
            if attrs["karat"] not in (18, 21, 24):
                raise serializers.ValidationError({"karat": "العيار يجب أن يكون 18 أو 21 أو 24."})
        return attrs


class TransactionDeleteSerializer(serializers.Serializer):
    # سبب الحذف إلزامي (Soft Delete)
    delete_reason = serializers.CharField(max_length=180)

    def validate_delete_reason(self, v):
        if not str(v).strip():
            raise serializers.ValidationError("سبب الحذف مطلوب.")
        return v

class PortfolioReportSerializer(serializers.Serializer):
    display_currency = serializers.CharField()
    gold = serializers.DictField()
    silver = serializers.DictField()
    cash = serializers.DictField()
    total = serializers.DictField()
    raw_holdings = serializers.DictField()

class ZakatOverviewSerializer(serializers.Serializer):
    portfolio = PortfolioReportSerializer()
    zakat = serializers.DictField()

class TransactionsReportSerializer(serializers.Serializer):
    # سنرجّع قائمة معاملات مع فلترة، بدون بنية معقّدة
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = serializers.ListField()
