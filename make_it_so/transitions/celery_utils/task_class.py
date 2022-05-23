import datetime
import os

from django.utils import timezone
from celery import shared_task, Task
from celery.contrib import rdb
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from celery.exceptions import Ignore as IgnoreException
from celery.worker import state as worker_state
from opentelemetry import trace
import structlog

from transitions.celery_utils.context import TransitionTaskContext
from transitions.celery_utils.exceptions import (
    TaskRetryException, TaskFailureException, RETRY_FOR, THROWS
)
from transitions.celery_utils.request import TransitionRequest
from transitions.celery_utils.tracing import trace_method


logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


# I think this is what I need: https://opentelemetry.io/docs/instrumentation/python/cookbook/#manually-setting-span-context


CELERY_POOL_TYPE = os.environ['CELERY_POOL_TYPE']


DEFAULT_TASK_KWARGS = dict(
    bind=True,
    autoretry_for=RETRY_FOR,
    throws=tuple(THROWS),
    max_retries=80,  # high upper limit, so this can be decided by the Resource
    default_retry_delay=45,
    soft_time_limit=655,  # limit per retry, not total
    time_limit=660,
    # retry_backoff=1, retry_backoff_max=90, retry_jitter=False,
)

task_ready = worker_state.task_ready


EXHAUSTED_SIDE_EFFECTS_FOR_TRANSITION = {
    # do we need 'failure' side effects also? to implement this modify on_failure()
    # and ensure that when it is a TaskFailureException that duplicate events aren't fired
    'ensure_forward_dependencies_deleted': 'deletion_terminated',
    'ensure_deleted': 'deletion_terminated'
}


class TransitionTask(Task):
    # https://github.com/celery/celery/blob/master/celery/app/task.py

    Request = TransitionRequest

    @classmethod
    def get_task_kwargs(cls):
        di = DEFAULT_TASK_KWARGS.copy()
        di['base'] = cls
        assert 'soft_time_limit' in di
        if 'time_limit' not in di:
            di['time_limit'] = di['soft_time_limit'] + 6

        assert isinstance(di['throws'], tuple)
        return di

    @trace_method
    def __call__(self, *args, **kwargs):
        # if self.transition.extra_task_kwargs:  # this is now done on client
        #     kwargs.update(transition.extra_task_kwargs)
        return self.run(*args, **kwargs)

    def get_transition(self, request, kwargs=None, use_cached=True):
        # invocation variables MUST be stored on the request, not the task obj
        # see: https://docs.celeryq.dev/en/stable/userguide/tasks.html?#instantiation
        # we're using getattr because some internal errors don't get custom request class
        obj = getattr(self.request, 'transition', None)
        if obj and use_cached:
            return obj
        logger.info('fetching Transition')
        request_kwargs = request.kwargs or {}
        kwargs = kwargs or {}
        kwargs = {**request_kwargs, **kwargs}
        if 'transition_pk' not in kwargs:
            logger.error('transition_pk missing on request')
            return None
        obj = TransitionTaskContext.fetch_transition(kwargs['transition_pk'])
        self.request.transition = obj
        return obj

    @property
    def task_context(self):
        return getattr(self.request, 'task_context', None)

    @property
    def tc(self):  # alias for task_context
        return self.task_context

    def get_task_age(self):
        if self.tc is None:
            return None
        started_at = self.tc.task_result_obj.date_created
        return (timezone.now() - started_at).seconds

    @property
    def retry_index(self):
        return self.request.retries

    def retry(
        self, args=None, kwargs=None, exc=None, throw=True, eta=None,
        countdown=None, max_retries=None, **options
    ):
        kwargs = kwargs or {}
        kwargs.update(self.request.kwargs)

        transition = self.get_transition(self.request, kwargs)
        if transition is None: # or self.tc is None:
            return super().retry(
                args=args, kwargs=kwargs, exc=exc, throw=throw, eta=eta,
                countdown=countdown, max_retries=max_retries, **options
            )

        countdown, reason = self.tc.resource_w.get_next_retry_countdown(
            self.retry_index, transition.type,
            task_age=self.get_task_age()
        )
        if countdown is None:
            self._notify_retries_exhausted(transition, kwargs, exc=exc)
            return self.simulate_failure(
                reason=reason, transition=transition,
                raise_exc=True, execute_hook=True
            )

        if 'countdown_override' in options:
            # this takes precedence over the Resource's value
            countdown = options.pop('countdown_override')

        kwargs['previous_retry_event'] = None  # clear out any prev value
        if isinstance(exc, TaskRetryException):
            kwargs['previous_retry_event'] = exc.details_tuple

        return super().retry(
            args=args, kwargs=kwargs, exc=exc, throw=throw, eta=eta,
            countdown=countdown, max_retries=max_retries, **options
        )

    def _notify_retries_exhausted(self, transition, task_kwargs, exc=None):
        if exc is None or isinstance(exc, TaskRetryException) is False:
            return

        reason, info = None, None
        details = task_kwargs.get('previous_retry_event')
        if details:
            _, reason, info = details

        side_effects = set()
        if transition.type in EXHAUSTED_SIDE_EFFECTS_FOR_TRANSITION:
            side_effects.add(
                EXHAUSTED_SIDE_EFFECTS_FOR_TRANSITION[transition.type]
            )
        if exc.exhausted_side_effect:
            side_effects.add(exc.exhausted_side_effect)

        for event_type in set(side_effects):
            reason = reason or 'retries_exhausted'
            self.log_resource_event(
                event_type, reason=reason, info=info, t=transition
            )

    def simulate_revoked(self, reason, request=None, raise_exc=True):

        request = request or self.request
        task_ready(request)

        self.backend.mark_as_revoked(
            request.id, reason, request=request
        )
        if raise_exc:
            exc = IgnoreException(f'simulated-revoke:{reason}')
            exc.reason = reason
            raise exc

    def _force_retry(self, reason, countdown, args, kwargs):
        if reason == 'potential_duplicate_task':
            kwargs['is_duplicate'] = True
        raise self.retry(
            exc=TaskRetryException(reason),
            countdown_override=countdown,
            args=args, kwargs=kwargs
        )

    @trace_method
    def before_start(self, task_id, args, kwargs):

        if self.tc is None:
            succ, _ = TransitionTaskContext.populate_context(self.request)
            if succ is False:
                return self.simulate_failure(
                    reason='failed to populate task context',
                    raise_exc=True, execute_hook=True
                )

        transition = t = self.get_transition(self.request, kwargs)
        is_rescheduled = kwargs.get('rescheduled', False)

        if transition.status == 'in_progress' and is_rescheduled is False:
            if self.retry_index == 0:
                # A potential duplicate task was submitted, trigger a delayed retry.
                # With this concurrent duplicates are still possible but less likely
                transition.log_event('potential_duplicate_task')
                return self._force_retry(
                    'potential_duplicate_task', 90, args, kwargs
                )

        if transition.status in ('succeeded', 'failed'):
            return self.simulate_revoked(
                f'revoking duplicate task, Transition.status: {t.status}'
            )

        if kwargs.get('is_duplicate', False) is True:
            del kwargs['is_duplicate']
            logger.info('proceeding with duplicate', task_id=task_id)

        expected = [('sent_to_broker' if self.retry_index == 0 else 'in_progress')]
        if is_rescheduled:
            expected = ['sent_to_broker', 'in_progress']

        if transition.status not in expected:
            logger.warning(
                'unexpected Transition.status', status=t.status, task_id=task_id,
                retry_index=self.retry_index, rescheduled=is_rescheduled
            )

        if self.retry_index == 0:
            transition.log_event('started')
            transition.celery_tasks.add(self.tc.task_result_obj)

    def on_retry(self, exc, task_id, args, kwargs, einfo):

        transition = self.get_transition(self.request, kwargs)
        if transition is None or self.tc is None:
            return logger.error(
                'on_retry() missing transition or task_context'
            )

        if isinstance(exc, TaskRetryException):
            self.log_events_for_exception(exc, einfo, transition)

        elif isinstance(exc, (SoftTimeLimitExceeded, TimeLimitExceeded)):
            transition.log_event('timeout')

        reason = exc.__str__()
        transition.log_event('retrying', reason=reason)

    @trace_method
    def on_success(self, retval, task_id, args, kwargs):
        transition = self.get_transition(self.request, kwargs)
        if transition is None:
            return logger.error('on_success() unable to fetch transition')

        transition.log_event('succeeded')

    def simulate_failure(
        self, reason=None, raise_exc=True, execute_hook=False,
        request=None, transition=None
    ):
        logger.info('simulate_failure() called')
        request = request or self.request
        reason = reason or 'simulated_failure'

        exc = IgnoreException(reason)
        exc.event_type = 'terminal_failure'
        exc.reason = reason

        task_ready(request, successful=False)
        self.backend.mark_as_failure(
            request.id, exc, traceback=None, request=request
        )

        if execute_hook:
            self.on_failure(
                None, request.id, request.args, request.kwargs,
                None, transition=transition
            )

        if raise_exc:
            raise exc

    def on_failure(
        self, exc, task_id, args, kwargs, einfo, transition=None
    ):
        transition = transition or self.get_transition(
            self.request, kwargs, use_cached=False
        )
        if transition is None:
            return logger.error('on_failure() unable to fetch transition')

        self.log_events_for_exception(exc, einfo, transition, failure=True)

    def log_events_for_exception(self, exc, einfo, transition, failure=False):

        if isinstance(exc, (TaskRetryException, TaskFailureException)):
            return self.log_resource_event(
                exc.event_type, reason=exc.reason, transition=transition,
                extra_info=exc.extra_info, exc=exc, einfo=einfo
            )
        if isinstance(exc, (SoftTimeLimitExceeded, TimeLimitExceeded)):
            transition.log_event('timeout')

        if failure:
            if isinstance(exc, IOError) and self.retry_index == 0:
                trace_str = einfo.traceback if einfo else None
                logger.warning(
                    'unhandled IOError', exception=str(exc), trace=trace_str
                )

            reason = getattr(exc, 'reason', 'exception')
            if isinstance(exc, TaskFailureException):
                reason = exc.event_type_and_reason

            self.log_resource_event(
                'terminal_failure', reason=reason,
                transition=transition
            )
            if transition.status != 'failed':  # avoid duplicate event
                transition.log_event('terminal_failure')

    def log_resource_event(
        self, event_type, reason=None, extra_info=None, info=None,
        transition=None, t=None, exc=None, einfo=None  # no 'next_state' arg
    ):
        extra_info = extra_info or info

        transition = transition or t or self.get_transition(self.request)
        if transition is None:
            return logger.warning('log_event() has no transition')

        transition.resource.log_event(
            event_type, reason=reason, t=transition,
            extra_info=extra_info, exc=exc, einfo=einfo
        )
