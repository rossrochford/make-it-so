from celery import shared_task, Task
import structlog

from transitions.celery_utils.task_class import TransitionTask


logger = structlog.get_logger(__name__)


@shared_task(**TransitionTask.get_task_kwargs())
def ensure_updated(self, transition_pk, **kwargs):

    c = self.task_context
    return c.resource_w.do_update()
