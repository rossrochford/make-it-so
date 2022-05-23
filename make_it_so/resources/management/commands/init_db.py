import json
import os
from os.path import expanduser as expand_path

from django.core.management.base import BaseCommand
import structlog

from users.models import AccountModel, UserModel, ProjectModel


logger = structlog.get_logger(__name__)


_credentials_fp = expand_path('~/Documents/declarative-test-1-65c13f721b7d.json')

GCP_CREDENTIALS_FILEPATH = os.environ.get(
    'GCP_CREDENTIALS_FILEPATH', _credentials_fp
)


class Command(BaseCommand):

    def handle(self, *args, **kwargs):

        user = UserModel.objects.filter(username='nobody').first()
        if user is None:
            user = UserModel.objects.create_superuser(
                'nobody', 'nobody@gmail.com', 'nobody'
            )

        account, created = AccountModel.objects.get_or_create(
            name='nobody_account', slug='nobody-account'
        )
        user.account = account
        user.save()
