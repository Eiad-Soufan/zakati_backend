from django.contrib import admin
from .models import Profile, UserSettings, Notification, MetalPrice, FxRate

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "full_name", "country", "city", "snapshot_version", "updated_at")
    search_fields = ("user__username", "full_name", "phone_number", "country", "city")

@admin.register(UserSettings)
class UserSettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "display_currency", "updated_at")

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "type", "title", "priority", "created_at", "read_at")
    list_filter = ("type", "priority")

@admin.register(MetalPrice)
class MetalPriceAdmin(admin.ModelAdmin):
    list_display = ("metal", "price_per_gram", "currency", "source", "fetched_at")
    list_filter = ("metal", "currency", "source")

@admin.register(FxRate)
class FxRateAdmin(admin.ModelAdmin):
    list_display = ("base", "quote", "rate", "source", "fetched_at")
    list_filter = ("base", "quote", "source")
