import os, uuid, base64, hashlib, json
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum
from io import BytesIO
from PIL import Image
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from .models import (
    Profile, UserSettings, Notification,
    MetalPrice, FxRate, Transaction
)

# -----------------------------
# رفع الصور Base64 → media URL
# -----------------------------
ALLOWED_IMAGE_TYPES = {"jpeg", "png", "webp"}
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2MB
def _decode_base64_image(data_uri: str):
    """
    يقبل 'data:image/png;base64,...' أو Base64 خام.
    يرجّع: raw bytes + الامتداد ('jpg'/'png'/'webp')
    """
    if not data_uri:
        raise ValueError("empty image")

    # افصل الهيدر لو موجود
    if "," in data_uri:
        _, b64 = data_uri.split(",", 1)
    else:
        b64 = data_uri

    raw = base64.b64decode(b64)
    # استخدم Pillow لاكتشاف النوع
    with Image.open(BytesIO(raw)) as img:
        fmt = (img.format or "").lower()  # 'jpeg','png','webp',...
    ext = "jpg" if fmt == "jpeg" else fmt

    if ext not in ("jpg", "jpeg", "png", "webp"):
        raise ValueError(f"unsupported image format: {ext}")
    return raw, ext


def save_base64_image_to_media(data_uri: str, folder: str = "uploads") -> str:
    """
    يحفظ الصورة عبر DEFAULT storage (Cloudinary عندنا) ويُرجع URL النهائي.
    """
    raw, ext = _decode_base64_image(data_uri)
    filename = f"{folder}/{uuid.uuid4().hex}.{ext}"
    path = default_storage.save(filename, ContentFile(raw))
    return default_storage.url(path)


# -----------------------------
# أسعار ومعاملات مساعدة
# -----------------------------
def latest_metal_dict():
    """أحدث أسعار ذهب/فضة لكل غرام (إن وجدت)."""
    result = {"gold_g_per": None, "silver_g_per": None, "currency": "USD", "fetched_at": None}
    gold = MetalPrice.objects.filter(metal="GOLD").order_by("-fetched_at").first()
    silver = MetalPrice.objects.filter(metal="SILVER").order_by("-fetched_at").first()
    if gold:
        result["gold_g_per"] = str(gold.price_per_gram)
        result["currency"] = gold.currency
        result["fetched_at"] = gold.fetched_at.isoformat()
    if silver:
        result["silver_g_per"] = str(silver.price_per_gram)
        if not result["fetched_at"]:
            result["fetched_at"] = silver.fetched_at.isoformat()
    return result

def latest_fx_for_pairs(pairs):
    """يرجع أحدث معدل لكل زوج إن وجد."""
    out = []
    seen = set()
    for base, quote in pairs:
        key = (base.upper(), quote.upper())
        if key in seen:
            continue
        seen.add(key)
        rec = FxRate.objects.filter(base=key[0], quote=key[1]).order_by("-fetched_at").first()
        if rec:
            out.append({
                "base": rec.base,
                "quote": rec.quote,
                "rate": str(rec.rate),
                "fetched_at": rec.fetched_at.isoformat()
            })
    return out

# -----------------------------
# حساب الأرصدة من المعاملات
# -----------------------------
def _sum_sign(expr_add, expr_withdraw, expr_zakat):
    return (expr_add or Decimal("0")) - (expr_withdraw or Decimal("0")) - (expr_zakat or Decimal("0"))

def compute_holdings(user):
    """
    يحسب أرصدة الذهب/الفضة/الأموال من معاملات المستخدم الفعالة (غير المحذوفة).
    """
    active = Transaction.objects.filter(user=user, soft_deleted_at__isnull=True)

    # GOLD by karat
    gold = {}
    gold_pure = Decimal("0")
    for karat in (18, 21, 24):
        qs = active.filter(asset_type="GOLD", karat=karat)
        add_w = qs.filter(operation_type="ADD").aggregate(s=Sum("weight_g"))["s"]
        out_w = qs.filter(operation_type="WITHDRAW").aggregate(s=Sum("weight_g"))["s"]
        zak_w = qs.filter(operation_type="ZAKAT").aggregate(s=Sum("weight_g"))["s"]
        bal = _sum_sign(add_w, out_w, zak_w)
        if bal > 0:
            gold[karat] = bal
            gold_pure += (bal * Decimal(karat) / Decimal(24))

    # SILVER
    qs_s = active.filter(asset_type="SILVER")
    add_s = qs_s.filter(operation_type="ADD").aggregate(s=Sum("weight_g"))["s"]
    out_s = qs_s.filter(operation_type="WITHDRAW").aggregate(s=Sum("weight_g"))["s"]
    zak_s = qs_s.filter(operation_type="ZAKAT").aggregate(s=Sum("weight_g"))["s"]
    silver_g = _sum_sign(add_s, out_s, zak_s)
    if silver_g < 0:
        silver_g = Decimal("0")

    # CASH wallets by currency
    # CASH wallets (grouped)
    wallets = compute_cash_wallets(user)


    gold_by_karat = [{"karat": k, "weight_g": str(gold[k])} for k in (18, 21, 24) if k in gold]

    return {
        "gold_by_karat": gold_by_karat,
        "gold_pure_g": str(gold_pure),
        "silver_g": str(silver_g if silver_g > 0 else Decimal("0")),
        "cash_wallets": wallets,
    }

def recent_transactions(user, limit=20):
    qs = Transaction.objects.filter(user=user, soft_deleted_at__isnull=True).order_by("-created_at")[:limit]
    items = []
    for tx in qs:
        items.append({
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
    return items

# -----------------------------
# Snapshot + نسخة
# -----------------------------
def bump_snapshot_version(user):
    p = user.profile
    p.snapshot_version += 1
    p.save(update_fields=["snapshot_version", "updated_at"])

def build_snapshot(user):
    profile = user.profile
    settings_obj = user.usersettings

    notifications_qs = user.notifications.all()[:20]
    assets = compute_holdings(user)
    txs = recent_transactions(user, limit=20)

    # ETag بسيط
    base = f"{profile.snapshot_version}:{profile.updated_at.isoformat()}".encode("utf-8")
    etag = 'sv-' + hashlib.sha256(base).hexdigest()[:16]

    return {
        "version": profile.snapshot_version,
        "etag": etag,
        "generated_at": timezone.now(),
        "profile": {
            "full_name": profile.full_name,
            "phone_number": profile.phone_number,
            "country": profile.country,
            "city": profile.city,
            "avatar_url": profile.avatar_url,
            "is_complete": profile.is_complete(),
        },
        "settings": {
            "display_currency": settings_obj.display_currency,
            "user_fx_overrides": settings_obj.user_fx_overrides or {},
        },
        "assets": assets,
        "transactions": txs,
        "notifications": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "priority": n.priority,
                "created_at": n.created_at.isoformat(),
                "read_at": n.read_at.isoformat() if n.read_at else None,
            } for n in notifications_qs
        ],
    }

def cash_balance_for(user, currency_code: str) -> Decimal:
    """
    يرجع رصيد المحفظة لعملة محددة من المعاملات الفعالة.
    """
    cc = (currency_code or "").upper().strip()
    if not cc:
        return Decimal("0")
    active = Transaction.objects.filter(user=user, soft_deleted_at__isnull=True, asset_type="CASH", currency_code=cc)
    add_c = active.filter(operation_type="ADD").aggregate(s=Sum("amount"))["s"] or Decimal("0")
    out_c = active.filter(operation_type="WITHDRAW").aggregate(s=Sum("amount"))["s"] or Decimal("0")
    zak_c = active.filter(operation_type="ZAKAT").aggregate(s=Sum("amount"))["s"] or Decimal("0")
    return add_c - out_c - zak_c

def gold_balance_for(user, karat: int) -> Decimal:
    """
    يرجع رصيد الذهب (بالغرام) لعيار محدد من المعاملات الفعالة.
    """
    if karat not in (18, 21, 24):
        return Decimal("0")
    active = Transaction.objects.filter(
        user=user, soft_deleted_at__isnull=True, asset_type="GOLD", karat=karat
    )
    add_w = active.filter(operation_type="ADD").aggregate(s=Sum("weight_g"))["s"] or Decimal("0")
    out_w = active.filter(operation_type="WITHDRAW").aggregate(s=Sum("weight_g"))["s"] or Decimal("0")
    zak_w = active.filter(operation_type="ZAKAT").aggregate(s=Sum("weight_g"))["s"] or Decimal("0")
    return add_w - out_w - zak_w

def silver_balance_for(user) -> Decimal:
    """
    يرجع رصيد الفضة (بالغرام) من المعاملات الفعالة.
    """
    active = Transaction.objects.filter(
        user=user, soft_deleted_at__isnull=True, asset_type="SILVER"
    )
    add_w = active.filter(operation_type="ADD").aggregate(s=Sum("weight_g"))["s"] or Decimal("0")
    out_w = active.filter(operation_type="WITHDRAW").aggregate(s=Sum("weight_g"))["s"] or Decimal("0")
    zak_w = active.filter(operation_type="ZAKAT").aggregate(s=Sum("weight_g"))["s"] or Decimal("0")
    return add_w - out_w - zak_w

# ----- مساهمة مناقلة نقدية/ذهب/فضة باختصار -----
def _cash_tx_signed_amount(tx) -> Decimal:
    if tx.asset_type != "CASH" or tx.amount is None:
        return Decimal("0")
    sign = Decimal("1") if tx.operation_type == "ADD" else Decimal("-1")
    return sign * Decimal(tx.amount)

def _gold_tx_signed_weight(tx) -> Decimal:
    if tx.asset_type != "GOLD" or tx.weight_g is None:
        return Decimal("0")
    sign = Decimal("1") if tx.operation_type == "ADD" else Decimal("-1")
    return sign * Decimal(tx.weight_g)

def _silver_tx_signed_weight(tx) -> Decimal:
    if tx.asset_type != "SILVER" or tx.weight_g is None:
        return Decimal("0")
    sign = Decimal("1") if tx.operation_type == "ADD" else Decimal("-1")
    return sign * Decimal(tx.weight_g)

# ----- تحقّق عدم السالب عند الحذف (Soft Delete) -----
def can_soft_delete_tx(user, tx) -> (bool, str):
    """
    عند حذف مناقلة، نزيل مساهمتها.
    إذا كانت المناقلة تزيد الرصيد (ADD) فإزالتها تُنقص الرصيد، فيجب التأكد أن النتيجة لن تصبح سالبة.
    إذا كانت المناقلة تُنقص الرصيد (WITHDRAW/ZAKAT)، فإزالتها آمنة لأنها سترفع الرصيد.
    """
    if tx.asset_type == "CASH":
        if tx.operation_type == "ADD":
            # حذف ADD => طرح مقدارها من الرصيد الحالي
            bal_now = cash_balance_for(user, tx.currency_code)
            after = bal_now - Decimal(tx.amount or 0)
            if after < 0:
                return False, f"لا يمكن حذف هذه المناقلة لأنها ستجعل رصيد {tx.currency_code} سالبًا."
        return True, ""
    elif tx.asset_type == "GOLD":
        if tx.operation_type == "ADD":
            bal_now = gold_balance_for(user, tx.karat or 0)
            after = bal_now - Decimal(tx.weight_g or 0)
            if after < 0:
                return False, f"لا يمكن حذف هذه المناقلة لأنها ستجعل رصيد ذهب عيار {tx.karat} سالبًا."
        return True, ""
    elif tx.asset_type == "SILVER":
        if tx.operation_type == "ADD":
            bal_now = silver_balance_for(user)
            after = bal_now - Decimal(tx.weight_g or 0)
            if after < 0:
                return False, "لا يمكن حذف هذه المناقلة لأنها ستجعل رصيد الفضة سالبًا."
        return True, ""
    return False, "نوع أصل غير مدعوم."

# ----- تحقّق عدم السالب عند التعديل -----
def can_edit_tx_without_negative(user, old_tx, new_fields: dict) -> (bool, str):
    """
    نتحقق من الأثر الصافي: إزالة مساهمة المناقلة القديمة + إضافة مساهمة المناقلة المعدّلة.
    ندعم الحالات الثلاث: CASH و GOLD و SILVER.
    """
    # نوع العملية يجب أن يبقى ضمن {ADD, WITHDRAW, ZAKAT}
    new_op = new_fields.get("operation_type", old_tx.operation_type)
    if new_op not in ("ADD", "WITHDRAW", "ZAKAT"):
        return False, "نوع العملية غير صالح."

    asset = old_tx.asset_type

    if asset == "CASH":
        new_cc = (new_fields.get("currency_code", old_tx.currency_code) or "").upper()
        new_amount = Decimal(str(new_fields.get("amount", old_tx.amount or "0")))
        if new_amount <= 0:
            return False, "المبلغ يجب أن يكون أكبر من الصفر."

        # الرصيد الحالي حسب العملة القديمة والجديدة
        # 1) ننقص أثر القديمة من رصيدها
        bal_old_cc = cash_balance_for(user, old_tx.currency_code)
        old_signed = _cash_tx_signed_amount(old_tx)
        bal_after_removal_old = bal_old_cc - old_signed

        # 2) نضيف أثر الجديدة إلى رصيد عملتها
        if new_cc == old_tx.currency_code:
            # نفس العملة: نستبدل الأثر فقط
            sign_new = Decimal("1") if new_op == "ADD" else Decimal("-1")
            after = bal_after_removal_old + (sign_new * new_amount)
            if after < 0:
                return False, f"الرصيد غير كافٍ لعملة {new_cc} بعد التعديل."
        else:
            # عملة جديدة مختلفة:
            # رصيد العملة القديمة بعد إزالة الأثر يجب أن يبقى غير سالب
            if bal_after_removal_old < 0:
                return False, f"سيصبح رصيد {old_tx.currency_code} سالبًا بعد التعديل."

            # نتحقق من رصيد العملة الجديدة بإضافة الأثر الجديد
            bal_new_cc = cash_balance_for(user, new_cc)
            sign_new = Decimal("1") if new_op == "ADD" else Decimal("-1")
            after_new = bal_new_cc + (sign_new * new_amount)
            if after_new < 0:
                return False, f"الرصيد غير كافٍ لعملة {new_cc} بعد التعديل."

        return True, ""

    elif asset == "GOLD":
        new_karat = int(new_fields.get("karat", old_tx.karat or 0))
        if new_karat not in (18, 21, 24):
            return False, "عيار الذهب يجب أن يكون 18 أو 21 أو 24."
        new_w = Decimal(str(new_fields.get("weight_g", old_tx.weight_g or "0")))
        if new_w <= 0:
            return False, "الوزن يجب أن يكون أكبر من الصفر."

        # إزالة أثر القديمة من عيارها، ثم إضافة الجديدة إلى عيارها (قد يكون مختلفًا)
        bal_old_k = gold_balance_for(user, old_tx.karat or 0)
        old_signed = _gold_tx_signed_weight(old_tx)
        after_old = bal_old_k - old_signed
        if old_tx.karat == new_karat:
            sign_new = Decimal("1") if new_op == "ADD" else Decimal("-1")
            after = after_old + (sign_new * new_w)
            if after < 0:
                return False, f"الرصيد غير كافٍ لعيار {new_karat} بعد التعديل."
        else:
            if after_old < 0:
                return False, f"سيصبح رصيد عيار {old_tx.karat} سالبًا بعد التعديل."
            bal_new_k = gold_balance_for(user, new_karat)
            sign_new = Decimal("1") if new_op == "ADD" else Decimal("-1")
            after_new = bal_new_k + (sign_new * new_w)
            if after_new < 0:
                return False, f"الرصيد غير كافٍ لعيار {new_karat} بعد التعديل."
        return True, ""

    elif asset == "SILVER":
        new_w = Decimal(str(new_fields.get("weight_g", old_tx.weight_g or "0")))
        if new_w <= 0:
            return False, "الوزن يجب أن يكون أكبر من الصفر."

        bal_old = silver_balance_for(user)
        old_signed = _silver_tx_signed_weight(old_tx)
        after_remove = bal_old - old_signed
        sign_new = Decimal("1") if new_op == "ADD" else Decimal("-1")
        after = after_remove + (sign_new * new_w)
        if after < 0:
            return False, "الرصيد غير كافٍ للفضة بعد التعديل."
        return True, ""

    return False, "نوع أصل غير مدعوم."

from hijri_converter import Gregorian, Hijri
from django.utils import timezone as djtz
from .models import ZakatAnchor

def today_hijri():
    g = djtz.now().date()
    h = Hijri.from_gregorian(g.year, g.month, g.day)
    return h  # فيه .year, .month, .day

def add_one_hijri_year(h: Hijri) -> Hijri:
    # إضافة سنة هجرية: بساطةً نزيد year بواحد ونحافظ على اليوم/الشهر
    return Hijri(h.year + 1, h.month, min(h.day, 30))

from decimal import Decimal

NISAB_GOLD_G = Decimal("85")
NISAB_SILVER_G = Decimal("595")

def _get_gold_price_per_gram_in(currency_code: str, user) -> Decimal | None:
    """
    نحصل على سعر غرام الذهب بعملة معينة.
    المصدر: MetalPrice (بالدولار غالباً) + Fx (USD->currency) + overrides المستخدم إذا وُجد.
    """
    md = latest_metal_dict()
    if not md["gold_g_per"]:
        return None
    usd_per_g = Decimal(md["gold_g_per"])

    target = (currency_code or "USD").upper()
    if target == "USD":
        return usd_per_g

    # أولاً overrides
    ov_key = f"USD->{target}"
    rate = None
    ov = getattr(user.usersettings, "user_fx_overrides", {}) or {}
    if ov_key in ov:
        try:
            rate = Decimal(str(ov[ov_key]))
        except Exception:
            rate = None

    # إن لم يوجد override نأخذ من FxRate
    if rate is None:
        fx = latest_fx_for_pairs([("USD", target)])
        if fx:
            rate = Decimal(str(fx[0]["rate"]))

    if rate is None:
        return None

    return (usd_per_g * rate).quantize(Decimal("0.000001"))

def total_cash_value_in(currency_code: str, user) -> Decimal:
    """
    نحسب قيمة كل محافظ النقد بعملة واحدة موحدة.
    """
    target = (currency_code or "USD").upper()
    total = Decimal("0")
    # اعتمد على compute_holdings ليرجع المحافظ الحالية
    wallets = compute_holdings(user)["cash_wallets"]
    if not wallets:
        return total

    # بناء خريطة أسعار التحويل (أولوية: overrides ثم FxRate)
    # سنحوّل base -> target
    ov = getattr(user.usersettings, "user_fx_overrides", {}) or {}
    fx_map = {}
    pairs = []
    for w in wallets:
        base = w["currency_code"].upper()
        if base == target:
            continue
        pairs.append((base, target))
    if pairs:
        for rec in latest_fx_for_pairs(pairs):
            fx_map[(rec["base"], rec["quote"])] = Decimal(str(rec["rate"]))

    for w in wallets:
        base = w["currency_code"].upper()
        amt = Decimal(w["balance"])
        if base == target:
            total += amt
        else:
            # override؟
            ov_key = f"{base}->{target}"
            rate = None
            if ov_key in ov:
                try:
                    rate = Decimal(str(ov[ov_key]))
                except Exception:
                    rate = None
            if rate is None:
                rate = fx_map.get((base, target))
            if rate:
                total += (amt * rate)
            # إذا لا يوجد معدّل، نتجاهل تلك المحفظة مؤقتًا (لن تؤذي التذكير)
    return total

def meets_nisab_gold_pure(user) -> bool:
    gold_pure = Decimal(compute_holdings(user)["gold_pure_g"])
    return gold_pure >= NISAB_GOLD_G

def meets_nisab_silver(user) -> bool:
    silver_g = Decimal(compute_holdings(user)["silver_g"])
    return silver_g >= NISAB_SILVER_G

def meets_nisab_cash(user) -> bool:
    # نقارن النقد بقيمة 85g ذهب
    settings_obj = user.usersettings
    dc = (settings_obj.display_currency or "USD").upper()
    gold_ppg = _get_gold_price_per_gram_in(dc, user)
    if gold_ppg is None:
        return False  # بدون سعر لا نذكّر (آمن)
    cash_total = total_cash_value_in(dc, user)
    nisab_value = NISAB_GOLD_G * gold_ppg
    return cash_total >= nisab_value

def _ensure_anchor(user, group: str, meets: bool):
    """
    يثبّت/يعيد ضبط Anchor لمجموعة أصول واحدة.
    """
    anc, _ = ZakatAnchor.objects.get_or_create(user=user, asset_group=group)
    if meets:
        # إذا لا يوجد start => نبدأ اليوم
        if not (anc.start_hijri_year and anc.start_hijri_month and anc.start_hijri_day):
            h = today_hijri()
            due = add_one_hijri_year(h)
            anc.start_hijri_year, anc.start_hijri_month, anc.start_hijri_day = h.year, h.month, h.day
            anc.due_hijri_year, anc.due_hijri_month, anc.due_hijri_day = due.year, due.month, due.day
            anc.status = "ACTIVE"
            anc.save()
        else:
            # ثابت — لا شيء
            pass
    else:
        # إن كان مثبتًا من قبل، نعيد ضبطه (زال النصاب)
        if anc.start_hijri_year:
            anc.status = "RESET"
            anc.start_hijri_year = anc.start_hijri_month = anc.start_hijri_day = None
            anc.due_hijri_year = anc.due_hijri_month = anc.due_hijri_day = None
            anc.save()

def _hijri_to_gregorian(hy, hm, hd):
    g = Hijri(hy, hm, hd).to_gregorian()
    return g.year, g.month, g.day

def _time_until_due(anc: ZakatAnchor):
    """
    ترجع (mode, value)
    mode: "days" أو "hours"
    value: عدد الأيام/الساعات حتى موعد الاستحقاق (قد تكون سالبة بعد الموعد).
    - الإنتاج: "days"
    - الاختبار: "hours" بناءً على start + ZAKAT_TEST_CYCLE_DAYS
    """
    today = timezone.now()

    if getattr(settings, "ZAKAT_TEST_MODE", False):
        start_g = _anchor_start_gregorian(anc)
        if not start_g:
            return ("hours", None)
        due_dt = timezone.datetime.combine(
            start_g + timedelta(days=int(getattr(settings, "ZAKAT_TEST_CYCLE_DAYS", 1))),
            timezone.datetime.min.time(),
            tzinfo=timezone.utc,
        )
        delta = due_dt - today
        hours = int(delta.total_seconds() // 3600)
        return ("hours", hours)

    # الوضع العادي (هجري → ميلادي ثم أيام)
    due_g = _due_gregorian(anc)
    if not due_g:
        return ("days", None)
    due_dt = timezone.datetime.combine(due_g, timezone.datetime.min.time(), tzinfo=timezone.utc)
    days = (due_dt.date() - today.date()).days
    return ("days", days)


def _notify_once(user, type_, title, body, meta_key, priority="normal"):
    # منع التكرار بنفس meta_key
    if Notification.objects.filter(user=user, meta_key=meta_key).exists():
        return
    Notification.objects.create(
        user=user, type=type_, title=title, body=body, priority=priority, meta_key=meta_key
    )

def update_zakat_anchors_and_reminders(user):
    """
    تُدعى عند login/heartbeat.
    1) تحدّث Anchors حسب تحقق/سقوط النصاب.
    2) تولّد تذكيرات مراحل الاستحقاق دون تكرار.
    """
    # 1) تثبيت/إعادة ضبط Anchors
    _ensure_anchor(user, "GOLD_PURE", meets_nisab_gold_pure(user))
    _ensure_anchor(user, "SILVER", meets_nisab_silver(user))
    _ensure_anchor(user, "CASH_POOL", meets_nisab_cash(user))

    # 2) توليد التذكيرات
    for group, title_label in (
        ("GOLD_PURE", "زكاة الذهب"),
        ("SILVER", "زكاة الفضة"),
        ("CASH_POOL", "زكاة الأموال"),
    ):
        try:
            anc = ZakatAnchor.objects.get(user=user, asset_group=group)
        except ZakatAnchor.DoesNotExist:
            continue
        if not anc.due_hijri_year:
            continue  # لا حول مثبت
        mode, remaining = _time_until_due(anc)
        if remaining is None:
            continue

        if mode == "days":
            # مراحل الإنتاج (هجري): -3 / 0 / +3 وما سبق (10، 3)
            if remaining == 10:
                _notify_once(user, "ZAKAT_REMINDER", f"تذكير {title_label}", "تستحق الزكاة بعد 10 أيام.", f"ZK:{group}:{anc.due_hijri_year}-{anc.due_hijri_month}-{anc.due_hijri_day}:T-10", "important")
            elif remaining == 3:
                _notify_once(user, "ZAKAT_REMINDER", f"تذكير {title_label}", "تستحق الزكاة بعد 3 أيام.", f"ZK:{group}:{anc.due_hijri_year}-{anc.due_hijri_month}-{anc.due_hijri_day}:T-3", "important")
            elif remaining == 0:
                _notify_once(user, "ZAKAT_REMINDER", f"تذكير {title_label}", "اليوم يوم استحقاق الزكاة.", f"ZK:{group}:{anc.due_hijri_year}-{anc.due_hijri_month}-{anc.due_hijri_day}:T0", "important")
            elif remaining == -3:
                _notify_once(user, "ZAKAT_REMINDER", f"متابعة {title_label}", "مرّ 3 أيام على استحقاق الزكاة.", f"ZK:{group}:{anc.due_hijri_year}-{anc.due_hijri_month}-{anc.due_hijri_day}:T+3", "normal")

        else:  # mode == "hours" (وضع الاختبار)
            checkpoints = getattr(settings, "ZAKAT_TEST_REMINDERS_HOURS", [6, 1, 0, -6])
            # remaining = ساعات حتى الاستحقاق
            if remaining in checkpoints:
                tag = f"H{remaining:+d}"  # مثل H+0 أو H-6
                _notify_once(
                    user, "ZAKAT_REMINDER", f"تذكير {title_label}",
                    "اختبار: قرب موعد استحقاق الزكاة." if remaining > 0 else ("اختبار: اليوم الاستحقاق." if remaining == 0 else "اختبار: مضى وقت على الاستحقاق."),
                    f"ZKTEST:{group}:{anc.start_hijri_year}-{anc.start_hijri_month}-{anc.start_hijri_day}:{tag}",
                    "important" if remaining >= 0 else "normal"
                )

       

from django.conf import settings
from datetime import timedelta

def _anchor_start_gregorian(anc: ZakatAnchor):
    if not (anc.start_hijri_year and anc.start_hijri_month and anc.start_hijri_day):
        return None
    g = Hijri(anc.start_hijri_year, anc.start_hijri_month, anc.start_hijri_day).to_gregorian()
    return timezone.datetime(g.year, g.month, g.day, tzinfo=timezone.utc).date()

def _due_gregorian(anc: ZakatAnchor):
    if not (anc.due_hijri_year and anc.due_hijri_month and anc.due_hijri_day):
        return None
    g = Hijri(anc.due_hijri_year, anc.due_hijri_month, anc.due_hijri_day).to_gregorian()
    return timezone.datetime(g.year, g.month, g.day, tzinfo=timezone.utc).date()

from decimal import Decimal, ROUND_HALF_UP

def _quant(v: Decimal) -> str:
    # تقريب لطيف 6 منازل للمعادن و2 للأموال عند العرض
    return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def portfolio_value_in_display(user):
    """
    يحسب القيمة الحالية للمحفظة بعملة العرض:
    - قيمة الذهب (باستخدام وزن خالص × سعر غرام الذهب)
    - قيمة الفضة (وزن × سعر غرام الفضة)
    - مجموع النقد (محافظ متعددة محوّلة لعملة العرض)
    - الإجمالي
    يرجع dict جاهز للعرض.
    """
    dc = (user.usersettings.display_currency or "USD").upper()
    holdings = compute_holdings(user)

    # أسعار الغرام
    metals = latest_metal_dict()
    gold_ppg_dc = _get_gold_price_per_gram_in(dc, user)     # قد تكون None
    silver_ppg_dc = None
    if metals.get("silver_g_per"):
        # تحويل سعر غرام الفضة من USD إلى display_currency
        usd_silver = Decimal(str(metals["silver_g_per"]))
        if dc == "USD":
            silver_ppg_dc = usd_silver
        else:
            fx = latest_fx_for_pairs([("USD", dc)])
            if fx:
                silver_ppg_dc = (usd_silver * Decimal(str(fx[0]["rate"]))).quantize(Decimal("0.000001"))

    # قيم المعادن
    gold_val = Decimal("0")
    if gold_ppg_dc:
        gold_val = Decimal(str(holdings["gold_pure_g"])) * gold_ppg_dc

    silver_val = Decimal("0")
    if silver_ppg_dc:
        silver_val = Decimal(str(holdings["silver_g"])) * silver_ppg_dc

    # قيمة النقد
    cash_val = total_cash_value_in(dc, user)

    total_val = gold_val + silver_val + cash_val

    return {
        "display_currency": dc,
        "gold": {
            "pure_weight_g": holdings["gold_pure_g"],
            "value": _quant(gold_val) if gold_ppg_dc else None,
            "price_per_gram": str(gold_ppg_dc) if gold_ppg_dc else None,
        },
        "silver": {
            "weight_g": holdings["silver_g"],
            "value": _quant(silver_val) if silver_ppg_dc else None,
            "price_per_gram": str(silver_ppg_dc) if silver_ppg_dc else None,
        },
        "cash": {
            "value": _quant(cash_val),
        },
        "total": {
            "value": _quant(total_val),
        },
        "raw_holdings": holdings,  # نفس الملخص المستخدم في الواجهة الرئيسية (للشفافية)
    }

def zakat_overview_in_display(user):
    """
    يقدّر الزكاة (2.5%) إذا كان اليوم هو يوم الاستحقاق (تقدير عرض فقط).
    - ذهب: 2.5% من القيمة الحالية للذهب الخالص.
    - فضة: 2.5% من القيمة الحالية للفضة.
    - نقد: 2.5% من إجمالي النقد بعملة العرض.
    كما يعيد مواعيد الاستحقاق القادمة لكل مجموعة (إن وُجد Anchor).
    """
    from .models import ZakatAnchor
    dc = (user.usersettings.display_currency or "USD").upper()
    pf = portfolio_value_in_display(user)

    # تقدير 2.5%
    def _pct(v):
        return _quant(Decimal(v) * Decimal("0.025"))

    est = {
        "display_currency": dc,
        "gold": {"zakat_estimate": None},
        "silver": {"zakat_estimate": None},
        "cash": {"zakat_estimate": _pct(pf["cash"]["value"])},
        "total": {"zakat_estimate": None},
        "anchors": [],
    }

    if pf["gold"]["value"] is not None:
        est["gold"]["zakat_estimate"] = _pct(pf["gold"]["value"])
    if pf["silver"]["value"] is not None:
        est["silver"]["zakat_estimate"] = _pct(pf["silver"]["value"])

    # مجموع تقديري إن توافرت جميع القيم
    parts = [x for x in [est["gold"]["zakat_estimate"], est["silver"]["zakat_estimate"], est["cash"]["zakat_estimate"]] if x is not None]
    if parts:
        total = sum(Decimal(x) for x in parts)
        est["total"]["zakat_estimate"] = _quant(total)

    # مواعيد الاستحقاق القادمة (حسب Anchors)
    anchors = ZakatAnchor.objects.filter(user=user)
    for a in anchors:
        mode, remaining = _time_until_due(a)
        est["anchors"].append({
            "group": a.asset_group,
            "start_hijri": {"y": a.start_hijri_year, "m": a.start_hijri_month, "d": a.start_hijri_day},
            "due_hijri": {"y": a.due_hijri_year, "m": a.due_hijri_month, "d": a.due_hijri_day},
            "remaining": {"mode": mode, "value": remaining},
            "status": a.status,
        })

    return {"portfolio": pf, "zakat": est}


# api/utils.py
from decimal import Decimal
from django.db.models import Sum, Case, When, F, DecimalField, Q
from .models import Transaction

# --- تجميع محافظ النقد لكل عملة (يعتمد المعاملات الفعّالة فقط) ---
from decimal import Decimal
from django.db.models import Sum, Case, When, F, DecimalField
from .models import Transaction

def compute_cash_wallets(user):
    """
    يُرجع قائمة محافظ نقدية مُجمَّعة لكل عملة:
    [{"currency_code": "USD", "balance": "123.456000"}, ...]
    يعتمد فقط المعاملات غير المحذوفة (soft_deleted_at__isnull=True).
    ADD = موجب | WITHDRAW/ZAKAT = سالب.
    """
    qs = Transaction.objects.filter(
        user=user,
        asset_type="CASH",
        soft_deleted_at__isnull=True,
    )

    balance_expr = Sum(
        Case(
            When(operation_type="ADD", then=F("amount")),
            When(operation_type__in=["WITHDRAW", "ZAKAT"], then=-F("amount")),
            default=Decimal("0"),
            output_field=DecimalField(max_digits=24, decimal_places=10),
        )
    )

    rows = (qs.values("currency_code")
              .annotate(balance=balance_expr)
              .order_by("currency_code"))

    wallets = []
    for r in rows:
        cc = (r["currency_code"] or "").upper()
        bal = r["balance"] or Decimal("0")
        if cc and bal > 0:
            wallets.append({
                "currency_code": cc,
                "balance": f"{bal:.6f}",
            })
    return wallets



# === Pricing helpers for reports ===
from decimal import Decimal
from datetime import date

def _get_silver_price_per_gram_in(currency_code: str, user) -> Decimal | None:
    md = latest_metal_dict()  # USD per gram عادة
    if not md["silver_g_per"]:
        return None
    usd_per_g = Decimal(md["silver_g_per"])
    target = (currency_code or "USD").upper()
    if target == "USD":
        return usd_per_g

    # أولوية: Overrides ثم FxRate
    rate = None
    ov = getattr(user.usersettings, "user_fx_overrides", {}) or {}
    ov_key = f"USD->{target}"
    if ov_key in ov:
        try:
            rate = Decimal(str(ov[ov_key]))
        except Exception:
            rate = None
    if rate is None:
        fx = latest_fx_for_pairs([("USD", target)])
        if fx:
            rate = Decimal(str(fx[0]["rate"]))

    if rate is None:
        return None
    return (usd_per_g * rate).quantize(Decimal("0.000001"))


def _convert_money(amount: Decimal, base: str, target: str, user) -> Decimal | None:
    base = (base or "USD").upper()
    target = (target or "USD").upper()
    if base == target:
        return amount

    ov = getattr(user.usersettings, "user_fx_overrides", {}) or {}
    rate = None
    ov_key = f"{base}->{target}"
    if ov_key in ov:
        try:
            rate = Decimal(str(ov[ov_key]))
        except Exception:
            rate = None

    if rate is None:
        fx = latest_fx_for_pairs([(base, target)])
        if fx:
            rate = Decimal(str(fx[0]["rate"]))

    if rate is None:
        return None
    return (amount * rate).quantize(Decimal("0.0000001"))


def _parse_period(preset: str | None, dfrom: str | None, dto: str | None) -> tuple[date, date, str | None]:
    """
    يحوّل (preset أو من/إلى) إلى تاريخين [from, to] شامِلَين.
    """
    from datetime import date, timedelta
    today = date.today()

    if preset in {"last_month", "last_6_months", "last_year"}:
        if preset == "last_month":
            start = date(today.year, today.month, 1)
            # نهاية الشهر الحالي → استخدم اليوم كحد أعلى
            end = today
        elif preset == "last_6_months":
            # تقريب بسيط: 6*30 يومًا
            start = today - timedelta(days=6*30)
            end = today
        else:  # last_year
            start = today.replace(month=1, day=1)
            end = today
        return start, end, preset

    # تخصيص: كلا التاريخين مطلوبان
    if dfrom and dto:
        return date.fromisoformat(dfrom), date.fromisoformat(dto), None

    # افتراضي: آخر شهر
    start = date(today.year, today.month, 1)
    return start, today, "last_month"


def build_reports_dashboard(user, display_currency: str, date_from: date, date_to: date) -> dict:
    """
    يُجمّع التقارير للفترة المطلوبة بنفس شكل واجهة التقارير.
    - كل القيم المالية بعملة العرض.
    - الأوزان بالجرام (الواجهة تعرضها KG بقسمة 1000).
    """
    from .models import Transaction
    from django.db.models import Q

    cur = display_currency.upper()

    qs = Transaction.objects.filter(
        user=user,
        soft_deleted_at__isnull=True,
        date__gte=date_from,
        date__lte=date_to
    )

    # محضِّرات
    zero_val = Decimal("0")
    sections = {
        "added":     {"gold_v": zero_val, "cash_v": zero_val, "silver_v": zero_val,
                      "gold_pure_g": zero_val, "silver_g": zero_val},
        "withdrawn": {"gold_v": zero_val, "cash_v": zero_val, "silver_v": zero_val,
                      "gold_pure_g": zero_val, "silver_g": zero_val},
        "zakat_paid":{"gold_v": zero_val, "cash_v": zero_val, "silver_v": zero_val,
                      "gold_pure_g": zero_val, "silver_g": zero_val},
    }

    # أسعار الغرام (ذهب/فضة) بعملة العرض
    gold_ppg = _get_gold_price_per_gram_in(cur, user)      # موجودة سلفًا عندكم
    silver_ppg = _get_silver_price_per_gram_in(cur, user)

    # Helper: أين نضع المعاملة؟
    def bucket(op: str):
        return "added" if op == "ADD" else ("withdrawn" if op == "WITHDRAW" else "zakat_paid")

    for tx in qs:
        b = sections[bucket(tx.operation_type)]

        if tx.asset_type == "CASH" and tx.amount:
            cv = _convert_money(Decimal(tx.amount), tx.currency_code, cur, user)
            if cv is not None:
                b["cash_v"] += cv

        elif tx.asset_type == "GOLD" and tx.weight_g and tx.karat in (18, 21, 24):
            pure_g = (Decimal(tx.weight_g) * Decimal(tx.karat) / Decimal(24))
            b["gold_pure_g"] += pure_g
            if gold_ppg is not None:
                b["gold_v"] += (pure_g * gold_ppg)

        elif tx.asset_type == "SILVER" and tx.weight_g:
            wg = Decimal(tx.weight_g)
            b["silver_g"] += wg
            if silver_ppg is not None:
                b["silver_v"] += (wg * silver_ppg)

    def pack(sec):
        # نجمع الإجمالي ونُنسّق النصوص
        total_v = sec["gold_v"] + sec["cash_v"] + sec["silver_v"]
        return {
            "title": "",
            "total_value": f"{total_v.quantize(Decimal('0.01'))}",
            "gold":   {"value": f"{sec['gold_v'].quantize(Decimal('0.01'))}",
                       "pure_weight_g": f"{sec['gold_pure_g'].quantize(Decimal('0.000000'))}"},
            "cash":   {"value": f"{sec['cash_v'].quantize(Decimal('0.01'))}"},
            "silver": {"value": f"{sec['silver_v'].quantize(Decimal('0.01'))}",
                       "weight_g": f"{sec['silver_g'].quantize(Decimal('0.000000'))}"},
        }

    return {
        "display_currency": cur,
        "sections": {
            "added": pack(sections["added"]),
            "withdrawn": pack(sections["withdrawn"]),
            "zakat_paid": pack(sections["zakat_paid"]),
        }
    }

