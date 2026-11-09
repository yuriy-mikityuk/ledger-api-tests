from django.urls import path

from core import views

urlpatterns = [
    path("accounts/", views.AccountCreateView.as_view()),
    path("accounts/<int:pk>/", views.AccountDeleteView.as_view()),
    path("wallets/", views.WalletListView.as_view()),
    path("deposits/", views.DepositCreateView.as_view()),
    path("withdrawals/", views.WithdrawalCreateView.as_view()),
    path("webhooks/provider/", views.ProviderWebhookView.as_view()),
    path("quotes/", views.QuoteCreateView.as_view()),
    path("exchanges/", views.ExchangeCreateView.as_view()),
    path("transactions/", views.TransactionListView.as_view()),
]
