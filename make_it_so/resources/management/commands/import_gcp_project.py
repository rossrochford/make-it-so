import json
import os

from django.core.management.base import BaseCommand
import structlog

from users.models import AccountModel, ProjectModel


logger = structlog.get_logger(__name__)


def _prompt_for_credentials_filepath():
    while True:
        filepath = input('enter path of credentials file: ').strip()
        if not os.path.exists(filepath):
            print('file not found')
            continue
        try:
            with open(filepath) as f:
                json.loads(f.read())
            return filepath
        except Exception:
            print('failed to parse file')
            continue


class Command(BaseCommand):

    def handle(self, *args, **kwargs):

        account_obj = AccountModel.objects.filter(slug='nobody-account').first()
        if account_obj is None:
            exit('expected nobody-account, please run: python manage.py init_db')

        credentials_filepath = _prompt_for_credentials_filepath()
        ProjectModel.get_or_create_gcp_project_model(
            credentials_filepath, account_obj
        )
