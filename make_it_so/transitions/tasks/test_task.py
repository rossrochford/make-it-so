
from celery import shared_task, Task
from celery.contrib import rdb
import gevent

from transitions.celery_utils.exceptions import TaskRetryException
from transitions.celery_utils.task_class import TransitionTask


@shared_task(**TransitionTask.get_task_kwargs())
def test_task(self, transition_pk, **kwargs):

    if self.retry_index == 0:
        self.log_resource_event('sleeping')
        gevent.sleep(65)

    raise TaskRetryException('creation_request_failed')

    if self.retry_index <= 3:
        gevent.sleep(3)
        raise TaskRetryException(f'fake retry')
        # raise ObscureException('im obscure')

    return 'yes boy'
