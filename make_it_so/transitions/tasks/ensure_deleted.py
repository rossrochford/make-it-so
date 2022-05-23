
import gevent

from celery import shared_task, Task
import structlog

from transitions.celery_utils.exceptions import TaskRetryException
from transitions.celery_utils.task_class import TransitionTask
from transitions.celery_utils import Memorize


logger = structlog.get_logger(__name__)


@Memorize(cache_keys=["transition_pk"])
def checkpoint__attempt_deletion(self, transition_pk, resource_w):
    self.log_resource_event('deleting')
    success, response = resource_w.delete_resource()
    return success, response


@shared_task(**TransitionTask.get_task_kwargs())
def ensure_deleted(self, transition_pk, **kwargs):

    c = self.task_context

    exists, _ = c.resource_w.check_exists(
        cached_existing=c.cached_existing
    )
    if not exists:
        # should we wait and re-check in case of race-condition?
        self.log_resource_event('resource_not_found', 'absent_before_deletion')
        c.resource_w.deleted_hook()
        return True

    succ, resp = checkpoint__attempt_deletion(self, c.t.pk, c.resource_w)
    if succ is False:
        raise TaskRetryException(
            'deletion_request_failed', info={'resp': resp}
        )

    self.log_resource_event(
        'deletion_request_succeeded', info={'resp': resp}
    )

    for i in range(10):
        gevent.sleep(3)
        exists, _ = c.resource_w.check_exists()
        if not exists:
            self.log_resource_event('resource_not_found', 'absent_after_deletion')
            c.resource_w.deleted_hook()
            return True

    # not using event_type 'resource_found' because it'll
    # write stale data to the resource
    raise TaskRetryException('not_yet_absent')
