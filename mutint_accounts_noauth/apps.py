from django.apps import AppConfig


class AccountsNoauthConfig(AppConfig):
    name = 'mutint_accounts_noauth'
    auth_app = True
