# api/providers.py
from __future__ import annotations
import time
import math
import requests
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from django.conf import settings
from django.utils import timezone
from .models import MetalPrice, FxRate

OZ_TO_G = Decimal("31.1034768")  # تحويل الأونصة للغرام

class Http:
    @staticmethod
    def get(url, headers=None, params=None, timeout=None, retries=0):
        timeout = timeout or settings.RATES_HTTP_TIMEOUT
        last_exc = None
        for i in range(retries + 1):
            try:
                r = requests.get(url, headers=headers or {}, params=params or {}, timeout=timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_exc = e
                if i < retries:
                    time.sleep(0.5 * (i + 1))
        raise last_exc

# ================= FX Providers =================
class FxProviderBase:
    def fetch(self, base: str, targets: List[str]) -> List[Tuple[str, str, Decimal]]:
        """returns list of (base, quote, rate)"""
        raise NotImplementedError

class FxExchangerateHost(FxProviderBase):
    """
    مجاني: https://exchangerate.host/#/
    مثال: https://api.exchangerate.host/latest?base=USD&symbols=SYP,MYR
    """
    def fetch(self, base: str, targets: List[str]) -> List[Tuple[str, str, Decimal]]:
        if not targets:
            return []
        url = "https://api.exchangerate.host/latest"
        data = Http.get(url, params={"base": base, "symbols": ",".join(targets)},
                        retries=settings.RATES_HTTP_RETRIES)
        rates = data.get("rates") or {}
        out = []
        for q in targets:
            if q in rates and rates[q]:
                out.append((base, q, Decimal(str(rates[q]))))
        return out

# ================= Metals Providers =================
class MetalsProviderBase:
    def fetch_gold_silver_per_gram(self, currency: str) -> Dict[str, Decimal]:
        """returns dict: {'gold_g_per': Decimal, 'silver_g_per': Decimal, 'currency': 'USD'}"""
        raise NotImplementedError

class MetalsGoldAPI(MetalsProviderBase):
    """
    https://www.goldapi.io/
    - تحتاج مفتاح في Header: x-access-token: <API_KEY>
    - سعر الذهب/الفضة عادة بالأونصة.
    """
    def fetch_gold_silver_per_gram(self, currency: str) -> Dict[str, Decimal]:
        headers = {"x-access-token": settings.GOLDAPI_API_KEY} if settings.GOLDAPI_API_KEY else {}
        out = {"currency": currency}
        # GOLD
        gjson = Http.get(f"https://www.goldapi.io/api/XAU/{currency}", headers=headers,
                         retries=settings.RATES_HTTP_RETRIES)
        # price per ounce:
        gold_oz = Decimal(str(gjson.get("price", "0")))
        out["gold_g_per"] = (gold_oz / OZ_TO_G).quantize(Decimal("0.000001"))

        # SILVER
        sjson = Http.get(f"https://www.goldapi.io/api/XAG/{currency}", headers=headers,
                         retries=settings.RATES_HTTP_RETRIES)
        silver_oz = Decimal(str(sjson.get("price", "0")))
        out["silver_g_per"] = (silver_oz / OZ_TO_G).quantize(Decimal("0.000001"))
        return out

class MetalsApiCom(MetalsProviderBase):
    """
    https://metals-api.com/
    - endpoint: latest?access_key=...&base=USD&symbols=XAU,XAG
    - XAU, XAG = ounce per base currency inverted غالبًا (تحقق من الوثائق)
    ملاحظة: بعض المزودين يرجعون "USD per Ounce" وآخرون "Ounce per USD".
    نضبط الحساب وفق الوثائق.
    """
    def fetch_gold_silver_per_gram(self, currency: str) -> Dict[str, Decimal]:
        key = settings.METALSAPI_ACCESS_KEY
        base = settings.METALSAPI_BASE or "USD"
        params = {"access_key": key, "base": base, "symbols": "XAU,XAG"}
        data = Http.get("https://metals-api.com/api/latest", params=params,
                        retries=settings.RATES_HTTP_RETRIES)
        rates = data.get("rates") or {}
        # التفسير الشائع: 1 XAU = N base (أي "أونصة ذهب تساوي N من العملة الأساسية")
        # إذا base == currency المطلوب، تمام. وإلا نحتاج تحويل FX عبر FX Provider خارجي.
        if "XAU" not in rates or "XAG" not in rates:
            raise ValueError("Rates for XAU/XAG missing")
        gold_oz_in_base = Decimal(str(rates["XAU"]))  # base currency per 1 ounce gold
        silver_oz_in_base = Decimal(str(rates["XAG"]))

        # لو كانت base != currency المطلوب، نستخدم FX لاحقًا لتحويل base->currency قبل الحفظ.
        out = {
            "gold_oz_in_base": gold_oz_in_base,
            "silver_oz_in_base": silver_oz_in_base,
            "base": base,
            "currency": currency
        }
        return out

# ================= Storing helpers =================
def store_fx_rates(pairs: List[Tuple[str, str, Decimal]], source: str):
    now = timezone.now()
    for base, quote, rate in pairs:
        FxRate.objects.create(base=base.upper(), quote=quote.upper(), rate=rate, source=source, fetched_at=now)

def store_metal_prices_from_per_gram(gold_g: Optional[Decimal], silver_g: Optional[Decimal],
                                     currency: str, source: str):
    now = timezone.now()
    if gold_g:
        MetalPrice.objects.create(metal="GOLD", price_per_gram=gold_g, currency=currency.upper(),
                                  source=source, fetched_at=now)
    if silver_g:
        MetalPrice.objects.create(metal="SILVER", price_per_gram=silver_g, currency=currency.upper(),
                                  source=source, fetched_at=now)

def pick_fx_provider() -> Optional[FxProviderBase]:
    if not settings.ENABLE_FX_PROVIDER:
        return None
    name = (settings.FX_PROVIDER_NAME or "").lower()
    if name == "exchangerate_host":
        return FxExchangerateHost()
    return FxExchangerateHost()  # افتراضي بسيط

def pick_metals_provider() -> Optional[MetalsProviderBase]:
    if not settings.ENABLE_METALS_PROVIDER:
        return None
    name = (settings.METALS_PROVIDER_NAME or "").lower()
    if name == "goldapi":
        return MetalsGoldAPI()
    if name == "metalsapi":
        return MetalsApiCom()
    return None

def fetch_and_store_rates():
    """
    الدالة الرئيسية يُناديها أمر الإدارة:
    - FX: يجلب BASE -> TARGETS ويحفظ في FxRate
    - Metals: يجلب ذهب/فضة لكل غرام بعملة معينة
      - إن كانت نتيجة metals-api تعطي base مختلفة عن العملة المطلوبة، نحاول تحويلها عبر FX.
    """
    # FX
    fxp = pick_fx_provider()
    if fxp:
        base = (settings.FX_BASE_CURRENCY or "USD").upper()
        targets = [t.strip().upper() for t in settings.FX_TARGETS if t.strip()]
        targets = [t for t in targets if t != base]
        if targets:
            pairs = fxp.fetch(base, targets)
            store_fx_rates(pairs, source=getattr(settings, "FX_PROVIDER_NAME", "fx"))

    # Metals
    mp = pick_metals_provider()
    if mp:
        currency = (getattr(settings, "METALSAPI_BASE", "USD") if isinstance(mp, MetalsApiCom)
                    else (settings.FX_BASE_CURRENCY or "USD")).upper()
        res = mp.fetch_gold_silver_per_gram(currency=currency)
        # معالجة MetalsApiCom: قد تعود بأونصة بالعملة الأساسية
        if "gold_g_per" in res:
            store_metal_prices_from_per_gram(res.get("gold_g_per"), res.get("silver_g_per"),
                                             res.get("currency", currency), source=getattr(settings, "METALS_PROVIDER_NAME", "metals"))
        else:
            # لدينا gold_oz_in_base, silver_oz_in_base, base, currency المطلوب النهائي
            base = res["base"].upper()
            target = res["currency"].upper()
            gold_oz_in_base = Decimal(str(res["gold_oz_in_base"]))
            silver_oz_in_base = Decimal(str(res["silver_oz_in_base"]))
            if target != base:
                # نحتاج معدل base->target
                fxp2 = pick_fx_provider()
                if not fxp2:
                    raise ValueError("Need FX provider to convert metals base to target currency")
                pair = fxp2.fetch(base, [target])
                if not pair:
                    raise ValueError(f"FX {base}->{target} not available")
                rate = pair[0][2]
                gold_oz_in_target = (gold_oz_in_base * rate)
                silver_oz_in_target = (silver_oz_in_base * rate)
            else:
                gold_oz_in_target = gold_oz_in_base
                silver_oz_in_target = silver_oz_in_base

            gold_g = (gold_oz_in_target / OZ_TO_G).quantize(Decimal("0.000001"))
            silver_g = (silver_oz_in_target / OZ_TO_G).quantize(Decimal("0.000001"))
            store_metal_prices_from_per_gram(gold_g, silver_g, target,
                                             source=getattr(settings, "METALS_PROVIDER_NAME", "metals"))

