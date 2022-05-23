
import gevent

from celery import shared_task, Task
import structlog

from transitions.celery_utils.exceptions import TaskRetryException, TaskFailureException
from transitions.celery_utils.task_class import TransitionTask
from transitions.celery_utils import Memorize
from transitions.models import TransitionModel


logger = structlog.get_logger(__name__)



@Memorize(cache_keys=["transition_pk"])
def checkpoint__attempt_creation(self, transition_pk, resource_w):
    self.log_resource_event('creating')
    success, provider_id, response = resource_w.create_resource()
    return success, provider_id, response


def _done(self, reason, list_resp):
    c = self.task_context
    self.log_resource_event('resource_found', reason)
    c.resource_w.exists_hook(list_response=list_resp)
    TransitionModel.create_transition(
        c.obj, 'ensure_healthy', prev=c.transition
    )
    return True


@shared_task(**TransitionTask.get_task_kwargs())
def ensure_exists(self, transition_pk, **kwargs):
    c = self.task_context

    exists, list_resp = c.resource_w.check_exists(
        cached_existing=c.cached_existing
    )
    if exists:
        return _done(self, 'found_before_creation', list_resp)

    succ, provider_id, resp = checkpoint__attempt_creation(
        self, c.t.pk, c.resource_w
    )
    if succ is False:
        raise TaskRetryException(
            'creation_request_failed', info={'resp': resp}
        )

    self.log_resource_event(
        'creation_request_succeeded', info={'resp': resp}
    )
    c.resource_w.exists_hook(
        creation_response=resp, provider_id=provider_id
    )

    gevent.sleep(3)  # 1st attempt by check_exists() has no delay
    exists, list_resp = c.resource_w.check_exists(num_retries=10)
    if exists:
        return _done(self, 'found_after_creation', list_resp)

    raise TaskRetryException('resource_not_found')
