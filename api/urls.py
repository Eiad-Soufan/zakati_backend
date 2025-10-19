from django.urls import path
from .views import *

urlpatterns = [
    path("healthz/", health, name="healthz"),

    # Auth
    path("auth/register", register, name="register"),
    path("auth/login", login, name="login"),

    # Profile
    path("profile", profile_update, name="profile_update"),

    # Notifications / Sync
    path("sync/heartbeat", heartbeat, name="heartbeat"),
    path("notifications/delta", notifications_delta, name="notifications_delta"),
    path("notifications/<int:pk>/read", notification_mark_read, name="notification_mark_read"),
    path("rates", get_rates, name="get_rates"),
    path("rates/user", patch_user_rates, name="patch_user_rates"),
    path("assets/cash/add", cash_add, name="cash_add"),
    path("assets/cash/withdraw", cash_withdraw, name="cash_withdraw"),
    path("assets/cash/zakat", cash_zakat, name="cash_zakat"),
    path("assets/gold/add", gold_add, name="gold_add"),
    path("assets/gold/withdraw", gold_withdraw, name="gold_withdraw"),
    path("assets/gold/zakat", gold_zakat, name="gold_zakat"),
    path("assets/silver/add", silver_add, name="silver_add"),
    path("assets/silver/withdraw", silver_withdraw, name="silver_withdraw"),
    path("assets/silver/zakat", silver_zakat, name="silver_zakat"),
    path("transactions/<int:pk>/edit", transaction_edit, name="transaction_edit"),
    path("transactions/<int:pk>/delete", transaction_delete, name="transaction_delete"),
    path("reports/portfolio", report_portfolio, name="report_portfolio"),
    path("reports/zakat", report_zakat_overview, name="report_zakat_overview"),
    path("reports/transactions", report_transactions, name="report_transactions"),
    path("reports/dashboard", ReportsDashboardView.as_view(), name="reports-dashboard"),
]

