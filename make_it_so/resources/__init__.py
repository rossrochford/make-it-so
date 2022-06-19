
from celery.contrib import rdb
from django.conf import settings
from django.utils import timezone
import structlog

from resources.types import ExistenceEnum, HealthEnum


logger = structlog.get_logger(__name__)


def get_resource_classes():
    from django.apps import apps as app_core

    resource_classes = {}
    for app_name in settings.RESOURCE_APPS:
        app_config = app_core.get_app_config(app_name)
        for cls in app_config.get_resource_classes():
            key = f'{app_name}.{cls.__name__}'
            resource_classes[key] = cls

    return resource_classes


EVENT_SIDE_EFFECTS = {
    ('ensure_exists', 'resource_found', 'found_before_creation'): 'exists',
    ('ensure_exists', 'resource_found', 'found_after_creation'): 'exists',
    ('ensure_exists', 'terminal_failure', 'creation_request_failed'): 'creation_failed',  # max_retries exceeded due to failed creation attempt
    ('ensure_exists', 'terminal_failure', 'resource_not_found'): 'creation_failed',

    ('ensure_healthy', 'health_checks_succeeded'): 'healthy',
    ('ensure_healthy', 'health_checks_terminated'): 'unhealthy',

    ('ensure_deleted', 'deletion_terminated'): 'deletion_failed',
    ('ensure_deleted', 'resource_not_found'): 'deleted',

    ('test', 'terminal_failure', 'creation_request_failed'): 'creation_failed',

    'resource_found_and_healthy': 'healthy',

    ('ensure_dependencies_ready', 'terminal_failure'): 'creation_terminated',

    ('ensure_exists', 'terminal_failure'): 'creation_terminated',
    ('ensure_healthy', 'terminal_failure'): 'creation_terminated',

}

# retries_exhausted_awaiting_dependencies
# retries_exhausted


def log_activity_on_resource(resource_model, event_type):
    r = resource_model
    if event_type == 'resource_found':
        r.existence = ExistenceEnum.exists
        r.existence_last_checked_at = timezone.now()
    if event_type == 'resource_not_found':
        r.existence = ExistenceEnum.doesnt_exist
        r.existence_last_checked_at = timezone.now()
        r.health = HealthEnum.unhealthy
        r.health_last_checked_at = timezone.now()
    if event_type in ('health_checks_succeeded', 'resource_found_and_healthy'):
        r.existence = ExistenceEnum.exists  # health implies existence
        r.existence_last_checked_at = timezone.now()
        r.health = HealthEnum.healthy
        r.health_last_checked_at = timezone.now()
    if event_type in ('health_check_failed', 'health_checks_terminated'):
        r.health = HealthEnum.unhealthy
        r.health_last_checked_at = timezone.now()


def decide_next_state_from_event(transition_type, event_type, reason=None):
    keys = [
        (transition_type, event_type),
        event_type
    ]

    if reason:  # push the most specific key to the front
        keys.insert(
            0, (transition_type, event_type, reason)
        )

    for key in keys:
        if key in EVENT_SIDE_EFFECTS:
            return EVENT_SIDE_EFFECTS[key]

    return None
