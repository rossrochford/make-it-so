
from django.core.management.base import BaseCommand
import structlog

from transitions.models import TransitionModel
from transitions.types import TransitionStatusEnum
from make_it_so.celery import app


logger = structlog.get_logger(__name__)


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument('transition_pk', type=int)
        parser.add_argument('force_status', nargs='?', default=None)

    def handle(self, *args, **kwargs):

        transition_pk = kwargs['transition_pk']
        status = kwargs['force_status']

        transition = TransitionModel.objects.select_related('resource').filter(
            pk=transition_pk).first()

        if transition is None:
            exit(f'no Transition found with pk: {transition_pk}')

        if status:
            assert TransitionStatusEnum.has_value(status)
            transition.status = status
            transition.save()

        transition.celery_apply_async(app)
