from celery import shared_task
from celery.contrib import rdb
import structlog

from make_it_so.celery import app
from resources.models import ResourceModel
from transitions.models import TransitionModel


logger = structlog.get_logger(__name__)


@shared_task(bind=True)
def create_missing_transitions(self):

    for desired_state in ['healthy', 'deleted']:
        _create_missing_transitions(desired_state)

    return True


def _create_missing_transitions(desired_state):

    resources = ResourceModel.objects.filter(
        desired_state=desired_state
    ).exclude(state__in=[desired_state, 'creation_terminated'])[:500]
    resource_ids = [r.id for r in resources]

    outstanding_transitions = TransitionModel.objects.filter(
        resource__in=resource_ids
    ).exclude(status__in=('succeeded', 'failed'))

    to_exclude = [t.resource_id for t in outstanding_transitions]
    resources = [r for r in resources if r.id not in to_exclude]

    for resource_model in resources:
        transition_type = None

        if desired_state == 'healthy':
            # perhaps this can be optimized
            resource_class = resource_model.resource_class
            transition_type = resource_class.get_initial_transition_type()

        elif desired_state == 'deleted':
            transition_type = 'ensure_forward_dependencies_deleted'

        if transition_type:
            transition = TransitionModel.create_transition(
                resource_model, transition_type  # consider using bulk_create()
            )


@shared_task(bind=True)
def submit_transition_tasks(self):
    transitions = TransitionModel.objects.filter(status='pending')[:500]
    for t in transitions:
        t.celery_apply_async(app)
