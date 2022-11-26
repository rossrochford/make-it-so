from django.core.management.base import BaseCommand
import structlog

from users.models import AccountModel, UserModel


logger = structlog.get_logger(__name__)


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
