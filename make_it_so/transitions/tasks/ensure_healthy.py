from celery.contrib import rdb
from celery import shared_task, Task
from opentelemetry import trace
import structlog

from transitions.celery_utils.exceptions import TaskRetryException, TaskFailureException
from transitions.celery_utils.task_class import TransitionTask


logger = structlog.get_logger(__name__)


def _done(self):
    c = self.task_context
    self.log_resource_event('health_checks_succeeded')
    c.resource_w.healthy_hook()
    return True


def _hc_failed(hc_name):
    raise TaskRetryException(
        'health_check_failed', info={'hc_name': hc_name},
        exhausted_side_effect='health_checks_terminated'
        # note: this side effect is not set on the transition level to
        # keep more control over the termination signal
    )


@shared_task(**TransitionTask.get_task_kwargs())
def ensure_healthy(self, transition_pk, **kwargs):
    c = self.task_context

    health_checks = c.resource_w.health_checks
    if not health_checks:
        # no health checks so we'll simply confirm it exists
        if c.resource_w.check_exists():
            return _done(self)

        self.log_resource_event('resource_not_found')
        return _hc_failed('check_exists')

    if self.retry_index >= 2:
        # exists_hook() may previously have failed to execute, so try again
        exists, list_resp = c.resource_w.check_exists()
        if exists is False:  # should we terminate early?
            raise TaskRetryException('resource_not_found')
        self.log_resource_event('resource_found')
        c.resource_w.exists_hook(list_response=list_resp)

    for healthcheck_method in health_checks:
        # note: successes aren't cached, could use @Memorize on expensive HCs
        hc_name = healthcheck_method.__name__
        succ, is_final = healthcheck_method()
        if succ is False:
            if is_final:
                raise TaskFailureException(
                    'health_checks_terminated', info={'hc_name': hc_name}
                )
            return _hc_failed(hc_name)

    return _done(self)
