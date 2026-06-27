from django.apps import AppConfig


class AccountsNoauthConfig(AppConfig):
    name = 'aledb_accounts_noauth'
    auth_app = True
