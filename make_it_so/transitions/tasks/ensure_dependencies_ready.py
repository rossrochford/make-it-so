
from celery import shared_task, Task
from celery.contrib import rdb
import structlog

from transitions.celery_utils.task_class import TransitionTask
from transitions.celery_utils.exceptions import (
    TaskRetryException, TaskFailureException
)
from transitions.models import TransitionModel


logger = structlog.get_logger(__name__)

VALID_STATES = {
    'exists': ['exists', 'healthy'],
    'healthy': ['healthy']
}


def _done(self, dependencies):
    c = self.task_context

    if len(dependencies) == 0 and c.resource_w.HAS_DEPENDENCIES:
        logger.warning('no dependencies found but HAS_DEPENDENCIES=True')

    self.log_resource_event('dependencies_ready')
    TransitionModel.create_transition(
        c.obj, 'ensure_exists', prev=c.transition
    )
    return True


@shared_task(**TransitionTask.get_task_kwargs())
def ensure_dependencies_ready(self, transition_pk, **kwargs):
    c = self.task_context

    dependencies = c.obj.get_dependencies()

    if len(dependencies) == 0:
        return _done(self, dependencies)

    ready_state = 'healthy'  # todo: make this field-specific, configured on ResourceClass

    for field_name, dep in dependencies.items():
        if dep.state in VALID_STATES[ready_state]:
            continue
        info = {'dependency': dep.id, 'dependency_state': dep.state}

        cause_event_type = dep.state_cause.type if dep.state_cause else None
        if dep.state == 'creation_failed' or cause_event_type == 'terminal_failure':
            raise TaskFailureException('dependency_failed', info=info)

        logger.info('dependency not ready', state=dep.state)
        raise TaskRetryException(
            'dependencies_pending', reason='not_ready', info=info
        )

    return _done(self, dependencies)
