
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


def _done(self):
    c = self.task_context

    self.log_resource_event('forward_dependencies_absent')
    TransitionModel.create_transition(
        c.obj, 'ensure_deleted', prev=c.transition
    )
    return True


@shared_task(**TransitionTask.get_task_kwargs())
def ensure_forward_dependencies_deleted(self, transition_pk, **kwargs):
    c = self.task_context

    fw_dependencies = c.obj.get_forward_dependencies()

    if len(fw_dependencies) == 0:
        return _done(self)

    for dep in fw_dependencies:
        if dep.state == 'deleted':
            continue
        info = {'dependency': dep.id, 'dependency_state': dep.state}

        cause_event_type = dep.state_cause.type if dep.state_cause else None
        if dep.state == 'deletion_terminated' or cause_event_type == 'terminal_failure':
            raise TaskFailureException(
                'deletion_terminated', info=info
            )
        raise TaskRetryException(
            'dependency_deletion_pending', reason='not_ready', info=info
        )

    return _done(self)
