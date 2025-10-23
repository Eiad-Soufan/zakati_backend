from django.contrib.auth import get_user_model
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema

from .serializers import *
from .models import *
from .utils import *

from .serializers import RatesResponseSerializer
from .models import UserSettings
import hashlib, json
from .serializers import PortfolioReportSerializer, ZakatOverviewSerializer, TransactionsReportSerializer
from django.core.paginator import Paginator
from django.db.models import Q
# api/views.py
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from urllib.parse import urlencode
import requests

from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes


User = get_user_model()

@extend_schema(tags=["Health"])
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok", "service": "zakati-api"})

# ========== Auth ==========

@extend_schema(tags=["Auth"], request=RegisterSerializer, responses={201: dict})
@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.save()
    return Response({"result": "success", "message": "تم إنشاء الحساب. الرجاء تسجيل الدخول."}, status=201)

@extend_schema(tags=["Auth"], request=LoginSerializer, responses={200: BootstrapSnapshotSerializer})
@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.validated_data["user"]

    # JWT
    refresh = RefreshToken.for_user(user)
    access_token = str(refresh.access_token)
    refresh_token = str(refresh)

    # نعيد Snapshot كامل مباشرة بعد الدخول
    update_zakat_anchors_and_reminders(user)
    snap = build_snapshot(user)
    body = {
        "access": access_token,
        "refresh": refresh_token,
        "snapshot": snap
    }
    return Response(body, status=200)

# ========== Profile Update (UI-First) ==========
@extend_schema(tags=["Profile"], request=ProfileSerializer, responses={200: BootstrapSnapshotSerializer})
@api_view(["PATCH"])
def profile_update(request):
    profile = request.user.profile
    serializer = ProfileSerializer(instance=profile, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    bump_snapshot_version(request.user)  # أي تعديل = نسخة جديدة
    snap = build_snapshot(request.user)
    return Response({"result": "success", "snapshot": snap})

# ========== Notifications ==========

@extend_schema(tags=["Notifications"], responses={200: dict, 204: None})
@api_view(["GET"])
def heartbeat(request):
    """
    يعيد إن كانت أقسام تغيرت بدون تنزيلها.
    الآن نفحص الإشعارات فقط (مبسّط).
    """
    user = request.user
    update_zakat_anchors_and_reminders(user)
    since_id = request.query_params.get("after_id")  # اختياري
    changed = {"notifications": False}

    if since_id:
        changed["notifications"] = Notification.objects.filter(user=user, id__gt=since_id).exists()
    else:
        # إن لم يرسل after_id: نتحقق إن كان هناك أي غير مقروء كمؤشر لتغيّر
        changed["notifications"] = Notification.objects.filter(user=user, read_at__isnull=True).exists()

    if not any(changed.values()):
        return Response(status=204)

    return Response({
        "version": user.profile.snapshot_version,
        "changed_sections": changed
    })

@extend_schema(tags=["Notifications"], responses={200: dict})
@api_view(["GET"])
def notifications_delta(request):
    """
    يرجع الإشعارات الجديدة فقط بعد after_id (إن وُجد).
    """
    user = request.user
    after_id = request.query_params.get("after_id")
    qs = Notification.objects.filter(user=user)
    if after_id:
        qs = qs.filter(id__gt=after_id)
    qs = qs.order_by("id")[:50]
    items = NotificationSerializer(qs, many=True).data
    last_id = items[-1]["id"] if items else after_id or None
    return Response({"items": items, "last_id": last_id})

@extend_schema(tags=["Notifications"], responses={200: BootstrapSnapshotSerializer})
@api_view(["POST"])
def notification_mark_read(request, pk: int = None):
    user = request.user
    try:
        n = Notification.objects.get(user=user, pk=pk)
    except Notification.DoesNotExist:
        return Response({"detail": "غير موجود."}, status=404)
    n.mark_read()
    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({"result": "success", "snapshot": snap})


# ========== Rates ==========
@extend_schema(tags=["Rates"], responses={200: RatesResponseSerializer})
@api_view(["GET"])
def get_rates(request):
    user = request.user
    settings_obj = user.usersettings

    metals = latest_metal_dict()

    # أزواج الصرف التي تهم المستخدم الآن:
    # 1) من USD -> display_currency (لتحويل الذهب/الفضة إن كانت بالدولار)
    pairs = [("USD", settings_obj.display_currency.upper())]

    # 2) من overrides (مثل "USD->SYP": 13500)
    overrides = settings_obj.user_fx_overrides or {}
    for k in overrides.keys():
        try:
            base, quote = k.split("->", 1)
            pairs.append((base.strip().upper(), quote.strip().upper()))
        except Exception:
            pass

    fx = latest_fx_for_pairs(pairs)

    # ETag بسيط
    etag_src = json.dumps({
        "dc": settings_obj.display_currency,
        "metals": metals,
        "fx": fx,
        "ov": overrides,
    }, sort_keys=True).encode("utf-8")
    etag = "rates-" + hashlib.sha256(etag_src).hexdigest()[:16]

    # If-None-Match
    inm = request.headers.get("If-None-Match")
    if inm and inm == etag:
        return Response(status=204)

    data = {
        "display_currency": settings_obj.display_currency,
        "metals": metals,
        "fx": fx,
        "user_overrides": overrides,
        "etag": etag,
    }
    return Response(data, status=200)


@extend_schema(tags=["Rates"], request=UserSettingsSerializer, responses={200: dict})
@api_view(["PATCH"])
def patch_user_rates(request):
    """
    يغيّر المستخدم عملة العرض أو يضيف/يعدّل أسعار صرفه الخاصة به.
    أمثلة:
    {
      "display_currency": "SYP",
      "user_fx_overrides": {"USD->SYP": 13500.0}
    }
    """
    settings_obj = request.user.usersettings
    serializer = UserSettingsSerializer(instance=settings_obj, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)

    # تحقّق بسيط من صيغة المفاتيح في overrides إن وجدت
    ov = serializer.validated_data.get("user_fx_overrides")
    if ov:
        cleaned = {}
        for k, v in ov.items():
            if "->" not in k:
                return Response({"detail": f"صيغة المفتاح غير صحيحة: {k}. استخدم BASE->QUOTE مثل USD->SYP."}, status=400)
            base, quote = [x.strip().upper() for x in k.split("->", 1)]
            if len(base) != 3 or len(quote) != 3:
                return Response({"detail": f"رموز العملات يجب أن تكون 3 حروف: {k}."}, status=400)
            try:
                rate = float(v)
                if rate <= 0:
                    raise ValueError()
            except Exception:
                return Response({"detail": f"قيمة غير صالحة للمعدل: {k}={v}."}, status=400)
            cleaned[f"{base}->{quote}"] = rate
        serializer.validated_data["user_fx_overrides"] = cleaned

    serializer.save()

    # نحدّث نسخة اللقطة لأن الإعدادات تغيّرت
    bump_snapshot_version(request.user)
    snap = build_snapshot(request.user)
    return Response({"result": "success", "snapshot": snap}, status=200)

# ========== Assets: CASH (ADD) ==========
@extend_schema(
    tags=["Assets: Cash"],
    request=CashAddSerializer,
    responses={201: dict},
)
@extend_schema(tags=["Assets: Cash"], request=CashAddSerializer, responses={201: dict})
@api_view(["POST"])
def cash_add(request):
    """
    إضافة أموال نقدية لمحفظة معيّنة.
    body:
    {
      "currency_code": "USD",
      "amount": 100.0,
      "date": "2025-10-11",        // اختياري، افتراضي اليوم
      "notes": "إيداع",
      "invoice_base64": "data:image/png;base64,...." // اختياري
    }
    """
    user = request.user
    ser = CashAddSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    invoice_url = ""
    b64 = (data.get("invoice_base64") or "").strip()
    if b64:
        try:
            invoice_url = save_base64_image_to_media(b64, subdir="invoices")
        except ValueError as e:
            code = str(e)
            msg_map = {
                "empty_base64": "صورة غير صالحة.",
                "invalid_base64": "صيغة Base64 غير صحيحة.",
                "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
            }
            return Response({"detail": msg_map.get(code, "فشل حفظ الفاتورة.")}, status=400)

    tx = Transaction.objects.create(
        user=user,
        asset_type="CASH",
        operation_type="ADD",
        currency_code=data["currency_code"],
        amount=data["amount"],
        date=data.get("date") or None,
        notes=data.get("notes") or "",
        invoice_image_url=invoice_url,
    )

    # كل عملية كتابة ناجحة => bump + snapshot جديد
    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({"result": "success", "operation": "cash_add", "operation_id": tx.id, "snapshot": snap}, status=201)


# ========== Assets: CASH (WITHDRAW) ==========
@extend_schema(tags=["Assets: Cash"], request=CashWithdrawSerializer, responses={201: dict, 400: dict})
@api_view(["POST"])
def cash_withdraw(request):
    """
    سحب أموال نقدية من محفظة معيّنة (مع منع الرصيد السالب).
    body:
    {
      "currency_code": "USD",
      "amount": 50.0,
      "date": "2025-10-11",        // اختياري
      "notes": "سحب نقدي",
      "invoice_base64": "data:image/png;base64,...." // اختياري
    }
    """
    user = request.user
    ser = CashWithdrawSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    # منع الرصيد السالب
    current_bal = cash_balance_for(user, data["currency_code"])
    if data["amount"] > current_bal:
        return Response(
            {"detail": f"الرصيد غير كافٍ. الرصيد الحالي لـ {data['currency_code']}: {current_bal}."},
            status=400
        )

    invoice_url = ""
    b64 = (data.get("invoice_base64") or "").strip()
    if b64:
        try:
            invoice_url = save_base64_image_to_media(b64, subdir="invoices")
        except ValueError as e:
            code = str(e)
            msg_map = {
                "empty_base64": "صورة غير صالحة.",
                "invalid_base64": "صيغة Base64 غير صحيحة.",
                "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
            }
            return Response({"detail": msg_map.get(code, "فشل حفظ الفاتورة.")}, status=400)

    tx = Transaction.objects.create(
        user=user,
        asset_type="CASH",
        operation_type="WITHDRAW",
        currency_code=data["currency_code"],
        amount=data["amount"],
        date=data.get("date") or None,
        notes=data.get("notes") or "",
        invoice_image_url=invoice_url,
    )

    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({"result": "success", "operation": "cash_withdraw", "operation_id": tx.id, "snapshot": snap}, status=201)


# ========== Assets: CASH (ZAKAT) ==========
@extend_schema(tags=["Assets: Cash"], request=CashZakatSerializer, responses={201: dict, 400: dict})
@api_view(["POST"])
def cash_zakat(request):
    """
    دفع زكاة من أموال نقدية (مع منع الرصيد السالب).
    body:
    {
      "currency_code": "USD",
      "amount": 10.0,
      "date": "2025-10-11",        // اختياري
      "notes": "زكاة نقد",
      "invoice_base64": "data:image/png;base64,...." // اختياري
    }
    """
    user = request.user
    ser = CashZakatSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    # منع الرصيد السالب
    current_bal = cash_balance_for(user, data["currency_code"])
    if data["amount"] > current_bal:
        return Response(
            {"detail": f"الرصيد غير كافٍ. الرصيد الحالي لـ {data['currency_code']}: {current_bal}."},
            status=400
        )

    invoice_url = ""
    b64 = (data.get("invoice_base64") or "").strip()
    if b64:
        try:
            invoice_url = save_base64_image_to_media(b64, subdir="invoices")
        except ValueError as e:
            code = str(e)
            msg_map = {
                "empty_base64": "صورة غير صالحة.",
                "invalid_base64": "صيغة Base64 غير صحيحة.",
                "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
            }
            return Response({"detail": msg_map.get(code, "فشل حفظ الفاتورة.")}, status=400)

    tx = Transaction.objects.create(
        user=user,
        asset_type="CASH",
        operation_type="ZAKAT",
        currency_code=data["currency_code"],
        amount=data["amount"],
        date=data.get("date") or None,
        notes=data.get("notes") or "",
        invoice_image_url=invoice_url,
    )

    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({"result": "success", "operation": "cash_zakat", "operation_id": tx.id, "snapshot": snap}, status=201)


# ========== Assets: GOLD (ADD) ==========
@extend_schema(tags=["Assets: Gold"], request=GoldAddSerializer, responses={201: dict, 400: dict})
@api_view(["POST"])
def gold_add(request):
    """
    إضافة ذهب بعيار محدد (18/21/24).
    body: { "karat": 24, "weight_g": 10.5, "date": "YYYY-MM-DD"?, "notes": "...", "invoice_base64": "..."? }
    """
    user = request.user
    ser = GoldAddSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    invoice_url = ""
    b64 = (data.get("invoice_base64") or "").strip()
    if b64:
        try:
            invoice_url = save_base64_image_to_media(b64, subdir="invoices")
        except ValueError as e:
            code = str(e)
            msg_map = {
                "empty_base64": "صورة غير صالحة.",
                "invalid_base64": "صيغة Base64 غير صحيحة.",
                "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
            }
            return Response({"detail": msg_map.get(code, "فشل حفظ الفاتورة.")}, status=400)

    tx = Transaction.objects.create(
        user=user,
        asset_type="GOLD",
        operation_type="ADD",
        karat=data["karat"],
        weight_g=data["weight_g"],
        date=data.get("date") or None,
        notes=data.get("notes") or "",
        invoice_image_url=invoice_url,
    )

    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({"result": "success", "operation": "gold_add", "operation_id": tx.id, "snapshot": snap}, status=201)


# ========== Assets: GOLD (WITHDRAW) ==========
@extend_schema(
    tags=["Assets: Gold"],
    request=GoldWithdrawSerializer,
    responses={201: dict, 400: dict},
)
@extend_schema(tags=["Assets: Gold"], request=GoldWithdrawSerializer, responses={201: dict, 400: dict})
@api_view(["POST"])
def gold_withdraw(request):
    """
    سحب ذهب من عيار محدد (مع منع الرصيد السالب لذلك العيار).
    body: { "karat": 21, "weight_g": 3.0, "date": "YYYY-MM-DD"?, "notes": "...", "invoice_base64": "..."? }
    """
    user = request.user
    ser = GoldWithdrawSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    current_bal = gold_balance_for(user, data["karat"])
    if data["weight_g"] > current_bal:
        return Response({"detail": f"الرصيد غير كافٍ لعيار {data['karat']}. الرصيد الحالي: {current_bal} g."}, status=400)

    invoice_url = ""
    b64 = (data.get("invoice_base64") or "").strip()
    if b64:
        try:
            invoice_url = save_base64_image_to_media(b64, subdir="invoices")
        except ValueError as e:
            code = str(e)
            msg_map = {
                "empty_base64": "صورة غير صالحة.",
                "invalid_base64": "صيغة Base64 غير صحيحة.",
                "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
            }
            return Response({"detail": msg_map.get(code, "فشل حفظ الفاتورة.")}, status=400)

    tx = Transaction.objects.create(
        user=user,
        asset_type="GOLD",
        operation_type="WITHDRAW",
        karat=data["karat"],
        weight_g=data["weight_g"],
        date=data.get("date") or None,
        notes=data.get("notes") or "",
        invoice_image_url=invoice_url,
    )

    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({"result": "success", "operation": "gold_withdraw", "operation_id": tx.id, "snapshot": snap}, status=201)


# ========== Assets: GOLD (ZAKAT) ==========
@extend_schema(tags=["Assets: Gold"], request=GoldZakatSerializer, responses={201: dict, 400: dict})
@api_view(["POST"])
def gold_zakat(request):
    """
    دفع زكاة ذهب من عيار محدد (تُخصم وزنًا من ذلك العيار) مع منع السالب.
    body: { "karat": 24, "weight_g": 0.25, "date": "YYYY-MM-DD"?, "notes": "...", "invoice_base64": "..."? }
    """
    user = request.user
    ser = GoldZakatSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    current_bal = gold_balance_for(user, data["karat"])
    if data["weight_g"] > current_bal:
        return Response({"detail": f"الرصيد غير كافٍ لعيار {data['karat']}. الرصيد الحالي: {current_bal} g."}, status=400)

    invoice_url = ""
    b64 = (data.get("invoice_base64") or "").strip()
    if b64:
        try:
            invoice_url = save_base64_image_to_media(b64, subdir="invoices")
        except ValueError as e:
            code = str(e)
            msg_map = {
                "empty_base64": "صورة غير صالحة.",
                "invalid_base64": "صيغة Base64 غير صحيحة.",
                "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
            }
            return Response({"detail": msg_map.get(code, "فشل حفظ الفاتورة.")}, status=400)

    tx = Transaction.objects.create(
        user=user,
        asset_type="GOLD",
        operation_type="ZAKAT",
        karat=data["karat"],
        weight_g=data["weight_g"],
        date=data.get("date") or None,
        notes=data.get("notes") or "",
        invoice_image_url=invoice_url,
    )

    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({"result": "success", "operation": "gold_zakat", "operation_id": tx.id, "snapshot": snap}, status=201)

# ========== Assets: SILVER (ADD) ==========
@extend_schema(tags=["Assets: Silver"], request=SilverAddSerializer, responses={201: dict, 400: dict})
@api_view(["POST"])
def silver_add(request):
    """
    إضافة فضة (بالجرام).
    body: { "weight_g": 50.0, "date": "YYYY-MM-DD"?, "notes": "...", "invoice_base64": "..."? }
    """
    user = request.user
    ser = SilverAddSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    invoice_url = ""
    b64 = (data.get("invoice_base64") or "").strip()
    if b64:
        try:
            invoice_url = save_base64_image_to_media(b64, subdir="invoices")
        except ValueError as e:
            code = str(e)
            msg_map = {
                "empty_base64": "صورة غير صالحة.",
                "invalid_base64": "صيغة Base64 غير صحيحة.",
                "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
            }
            return Response({"detail": msg_map.get(code, "فشل حفظ الفاتورة.")}, status=400)

    tx = Transaction.objects.create(
        user=user,
        asset_type="SILVER",
        operation_type="ADD",
        weight_g=data["weight_g"],
        date=data.get("date") or None,
        notes=data.get("notes") or "",
        invoice_image_url=invoice_url,
    )

    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({"result": "success", "operation": "silver_add", "operation_id": tx.id, "snapshot": snap}, status=201)


# ========== Assets: SILVER (WITHDRAW) ==========
@extend_schema(tags=["Assets: Silver"], request=SilverWithdrawSerializer, responses={201: dict, 400: dict})
@api_view(["POST"])
def silver_withdraw(request):
    """
    سحب فضة (منع الرصيد السالب).
    body: { "weight_g": 5.0, "date": "YYYY-MM-DD"?, "notes": "...", "invoice_base64": "..."? }
    """
    user = request.user
    ser = SilverWithdrawSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    current_bal = silver_balance_for(user)
    if data["weight_g"] > current_bal:
        return Response({"detail": f"الرصيد غير كافٍ من الفضة. الرصيد الحالي: {current_bal} g."}, status=400)

    invoice_url = ""
    b64 = (data.get("invoice_base64") or "").strip()
    if b64:
        try:
            invoice_url = save_base64_image_to_media(b64, subdir="invoices")
        except ValueError as e:
            code = str(e)
            msg_map = {
                "empty_base64": "صورة غير صالحة.",
                "invalid_base64": "صيغة Base64 غير صحيحة.",
                "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
            }
            return Response({"detail": msg_map.get(code, "فشل حفظ الفاتورة.")}, status=400)

    tx = Transaction.objects.create(
        user=user,
        asset_type="SILVER",
        operation_type="WITHDRAW",
        weight_g=data["weight_g"],
        date=data.get("date") or None,
        notes=data.get("notes") or "",
        invoice_image_url=invoice_url,
    )

    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({"result": "success", "operation": "silver_withdraw", "operation_id": tx.id, "snapshot": snap}, status=201)


# ========== Assets: SILVER (ZAKAT) ==========
@extend_schema(tags=["Assets: Silver"], request=SilverZakatSerializer, responses={201: dict, 400: dict})
@api_view(["POST"])
def silver_zakat(request):
    """
    دفع زكاة الفضة (تُخصم وزنًا) مع منع السالب.
    body: { "weight_g": 1.25, "date": "YYYY-MM-DD"?, "notes": "...", "invoice_base64": "..."? }
    """
    user = request.user
    ser = SilverZakatSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    current_bal = silver_balance_for(user)
    if data["weight_g"] > current_bal:
        return Response({"detail": f"الرصيد غير كافٍ من الفضة. الرصيد الحالي: {current_bal} g."}, status=400)

    invoice_url = ""
    b64 = (data.get("invoice_base64") or "").strip()
    if b64:
        try:
            invoice_url = save_base64_image_to_media(b64, subdir="invoices")
        except ValueError as e:
            code = str(e)
            msg_map = {
                "empty_base64": "صورة غير صالحة.",
                "invalid_base64": "صيغة Base64 غير صحيحة.",
                "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
            }
            return Response({"detail": msg_map.get(code, "فشل حفظ الفاتورة.")}, status=400)

    tx = Transaction.objects.create(
        user=user,
        asset_type="SILVER",
        operation_type="ZAKAT",
        weight_g=data["weight_g"],
        date=data.get("date") or None,
        notes=data.get("notes") or "",
        invoice_image_url=invoice_url,
    )

    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({"result": "success", "operation": "silver_zakat", "operation_id": tx.id, "snapshot": snap}, status=201)

# ========== Transactions: Edit with Versioning ==========
@extend_schema(tags=["Transactions"], request=TransactionEditSerializer, responses={200: dict, 400: dict, 404: dict})
@api_view(["POST"])
def transaction_edit(request, pk: int):
    user = request.user
    try:
        old = Transaction.objects.get(user=user, pk=pk, soft_deleted_at__isnull=True)
    except Transaction.DoesNotExist:
        return Response({"detail": "المناقلة غير موجودة أو محذوفة."}, status=404)

    ser = TransactionEditSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    # جهّز حقول المناقلة الجديدة (ابدأ من القديمة)
    new_fields = {
        "operation_type": data.get("operation_type", old.operation_type),
        "currency_code": data.get("currency_code", old.currency_code),
        "amount": data.get("amount", old.amount),
        "karat": data.get("karat", old.karat),
        "weight_g": data.get("weight_g", old.weight_g),
        "date": data.get("date", old.date),
        "notes": data.get("notes", old.notes),
    }

    # تحقّق عدم السالب بعد التعديل
    ok, msg = can_edit_tx_without_negative(user, old, new_fields)
    if not ok:
        return Response({"detail": msg}, status=400)

    # معالجة فاتورة جديدة إن أُرسلت
    invoice_url = old.invoice_image_url or ""
    b64 = (data.get("invoice_base64") or "").strip()
    if b64:
        try:
            invoice_url = save_base64_image_to_media(b64, subdir="invoices")
        except ValueError as e:
            code = str(e)
            msg_map = {
                "empty_base64": "صورة غير صالحة.",
                "invalid_base64": "صيغة Base64 غير صحيحة.",
                "image_too_large": "حجم الصورة كبير (الحد 2MB).",
                "unsupported_type": "نوع الصورة غير مدعوم (PNG/JPEG/WEBP).",
            }
            return Response({"detail": msg_map.get(code, "فشل حفظ الفاتورة.")}, status=400)

    # أنشئ نسخة جديدة
    new_tx = Transaction.objects.create(
        user=user,
        asset_type=old.asset_type,
        operation_type=new_fields["operation_type"],
        karat=new_fields["karat"],
        weight_g=new_fields["weight_g"],
        currency_code=new_fields["currency_code"] or "",
        amount=new_fields["amount"],
        date=new_fields["date"],
        notes=new_fields["notes"],
        invoice_image_url=invoice_url,
        previous_version=old,
        is_edited=True,
        edit_reason=data["edit_reason"],
    )

    # أرشفة القديمة (لا تُحسب)
    from django.utils import timezone as djtz
    old.is_edited = True
    old.edit_reason = data["edit_reason"]
    old.soft_deleted_at = djtz.now()
    old.save(update_fields=["is_edited", "edit_reason", "soft_deleted_at"])

    # bump + snapshot
    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({
        "result": "success",
        "operation": "transaction_edit",
        "operation_id": new_tx.id,
        "previous_id": old.id,
        "snapshot": snap
    }, status=200)

# ========== Transactions: Soft Delete ==========
@extend_schema(tags=["Transactions"], request=TransactionDeleteSerializer, responses={200: dict, 400: dict, 404: dict})
@api_view(["POST"])
def transaction_delete(request, pk: int):
    user = request.user
    try:
        tx = Transaction.objects.get(user=user, pk=pk, soft_deleted_at__isnull=True)
    except Transaction.DoesNotExist:
        return Response({"detail": "المناقلة غير موجودة أو محذوفة."}, status=404)

    ser = TransactionDeleteSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    reason = ser.validated_data["delete_reason"]

    ok, msg = can_soft_delete_tx(user, tx)
    if not ok:
        return Response({"detail": msg}, status=400)

    from django.utils import timezone as djtz
    tx.is_edited = True  # نعلم أنها تغيرت (حُذفت)
    tx.edit_reason = reason
    tx.soft_deleted_at = djtz.now()
    tx.save(update_fields=["is_edited", "edit_reason", "soft_deleted_at"])

    bump_snapshot_version(user)
    snap = build_snapshot(user)
    return Response({
        "result": "success",
        "operation": "transaction_delete",
        "operation_id": tx.id,
        "snapshot": snap
    }, status=200)




# ========== Reports: Portfolio ==========
@extend_schema(tags=["Reports"], responses={200: PortfolioReportSerializer})
@api_view(["GET"])
def report_portfolio(request):
    """
    ملخص المحفظة بعملة العرض:
    - قيمة الذهب/الفضة/النقد والإجمالي
    - الأوزان/الأرصدة الخام (للتطابق مع الشاشة)
    """
    data = portfolio_value_in_display(request.user)
    return Response(data, status=200)


# ========== Reports: Zakat Overview ==========
@extend_schema(tags=["Reports"], responses={200: ZakatOverviewSerializer})
@api_view(["GET"])
def report_zakat_overview(request):
    """
    نظرة الزكاة:
    - تقدير 2.5% لكل مجموعة (قيمة حالية للعرض فقط)
    - نقاط الحَول (Anchors) والمتبقي للاستحقاق (أيام/ساعات في وضع الاختبار)
    """
    data = zakat_overview_in_display(request.user)
    return Response(data, status=200)


# ========== Reports: Transactions (filtered) ==========
@extend_schema(
    tags=["Reports"],
    responses={200: TransactionsReportSerializer},
    description="""
    فلاتر اختيارية:
    - asset_type: GOLD | SILVER | CASH
    - operation_type: ADD | WITHDRAW | ZAKAT
    - currency_code: رمز عملة للنقد (مثال USD)
    - karat: 18 | 21 | 24 للذهب
    - date_from, date_to: YYYY-MM-DD
    - search: نص في الملاحظات
    - page: رقم الصفحة (افتراضي 1), page_size: افتراضي 20
    """
)
@api_view(["GET"])
def report_transactions(request):
    user = request.user
    qs = Transaction.objects.filter(user=user, soft_deleted_at__isnull=True).order_by("-created_at")

    # فلاتر
    asset_type = request.query_params.get("asset_type")
    if asset_type in ("GOLD", "SILVER", "CASH"):
        qs = qs.filter(asset_type=asset_type)

    operation_type = request.query_params.get("operation_type")
    if operation_type in ("ADD", "WITHDRAW", "ZAKAT"):
        qs = qs.filter(operation_type=operation_type)

    cc = request.query_params.get("currency_code")
    if cc and asset_type in (None, "CASH"):
        qs = qs.filter(currency_code=cc.upper())

    karat = request.query_params.get("karat")
    if karat and asset_type in (None, "GOLD"):
        try:
            k = int(karat)
            if k in (18, 21, 24):
                qs = qs.filter(karat=k)
        except Exception:
            pass

    date_from = request.query_params.get("date_from")
    if date_from:
        qs = qs.filter(date__gte=date_from)

    date_to = request.query_params.get("date_to")
    if date_to:
        qs = qs.filter(date__lte=date_to)

    search = request.query_params.get("search")
    if search:
        qs = qs.filter(Q(notes__icontains=search))

    # ترقيم الصفحات
    page = int(request.query_params.get("page", 1))
    page_size = int(request.query_params.get("page_size", 20))
    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(page)

    # نفس شكل عناصر recent_transactions
    results = []
    for tx in page_obj.object_list:
        results.append({
            "id": tx.id,
            "asset_type": tx.asset_type,
            "operation_type": tx.operation_type,
            "karat": tx.karat,
            "weight_g": str(tx.weight_g) if tx.weight_g is not None else None,
            "currency_code": tx.currency_code or None,
            "amount": str(tx.amount) if tx.amount is not None else None,
            "date": tx.date.isoformat(),
            "notes": tx.notes,
            "invoice_image_url": tx.invoice_image_url or "",
            "is_edited": tx.is_edited,
        })

    data = {
        "count": paginator.count,
        "next": page_obj.next_page_number() if page_obj.has_next() else None,
        "previous": page_obj.previous_page_number() if page_obj.has_previous() else None,
        "results": results,
    }
    return Response(data, status=200)




# ========== Reports: Dashboard (UI) ==========
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.decorators import api_view
from rest_framework.response import Response

@extend_schema(
    tags=["Reports"],
    parameters=[
        OpenApiParameter(name="display_currency", required=False, type=str, description="عملة العرض (افتراضيًا من إعدادات المستخدم)"),
        OpenApiParameter(name="preset", required=False, type=str,
                         description="last_month | last_6_months | last_year (إن وُجد لا تستعمل من/إلى)"),
        OpenApiParameter(name="date_from", required=False, type=str, description="YYYY-MM-DD (للتخصيص)"),
        OpenApiParameter(name="date_to", required=False, type=str, description="YYYY-MM-DD (للتخصيص)"),
    ],
    responses={200: dict},
)
@api_view(["GET"])
def report_dashboard(request):
    """
    واجهة موحّدة للتقارير مطابقة للتصميم:
    - الأصول المضافة / المسحوبة / الزكاة المدفوعة
    - القيم بعملة العرض + أوزان الذهب الخالص والفضة
    - فلترة: preset أو date_from/date_to
    """
    user = request.user
    disp = (request.query_params.get("display_currency") or user.usersettings.display_currency).upper()

    preset = request.query_params.get("preset")
    dfrom = request.query_params.get("date_from")
    dto   = request.query_params.get("date_to")

    try:
        start, end, resolved = _parse_period(preset, dfrom, dto)
    except Exception:
        return Response({"detail": "صيغة التاريخ غير صحيحة."}, status=400)

    payload = build_reports_dashboard(user, disp, start, end)
    payload["period"] = {
        "preset": resolved,
        "from": start.isoformat(),
        "to":   end.isoformat(),
    }
    payload["generated_at"] = timezone.now().isoformat()
    # عنونة العناوين كما في الواجهة
    payload["sections"]["added"]["title"] = "الأصول المضافة"
    payload["sections"]["withdrawn"]["title"] = "الأصول المسحوبة"
    payload["sections"]["zakat_paid"]["title"] = "الزكاة المدفوعة"

    return Response(payload, status=200)




