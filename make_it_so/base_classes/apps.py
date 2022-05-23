from django.apps import AppConfig


class BaseClassesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'base_classes'
