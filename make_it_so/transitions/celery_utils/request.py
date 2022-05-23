from attrdict import AttrDict

from celery.contrib import rdb
from celery.worker import state as worker_state
from celery.worker.request import Request
import structlog
from opentelemetry import trace

from transitions.celery_utils.context import TransitionTaskContext
from transitions.celery_utils.tracing import StartSpanWithCarrier


logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

task_ready = worker_state.task_ready


class TransitionRequest(Request):
    # https://github.com/celery/celery/blob/master/celery/worker/request.py

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # request_dict populates task.request, a Context object
        self._request_dict['transition'] = None
        self._request_dict['task_context'] = None
        self._request_dict['previous_retry_event_type'] = None

    def get_context(self):
        context = self._request_dict.get('task_context')
        if context is None:
            request = AttrDict(self._request_dict)  # hack
            _, context = TransitionTaskContext.populate_context(request)
        return context

    def on_timeout(self, soft, timeout):
        # note: we're not calling super class impl here because it blindly
        # fails all hard time-outs, this isn't ideal, especially for gevent

        if soft:
            return logger.warning(
                'soft timeout', timeout=timeout, task_id=self.id
            )

        self.task.request.kwargs = self._context.kwargs  # hack bc kwargs are missing
        is_rescheduled = self.kwargs.get('rescheduled', False)
        task_context = self.get_context()  # not the same as self._context
        transition = task_context.t if task_context else None

        if self.task.acks_late and self.task.acks_on_failure_or_timeout:
            self.acknowledge()

        # carrier = task_context.carrier if task_context else None
        # with StartSpanWithCarrier(tracer, 'on-timeout', carrier):

        if is_rescheduled is False and self._context.retries < 2:
            if transition:
                transition.log_event(
                    'rescheduling', reason='hard_timeout'
                )
            self.task.simulate_revoked(
                'rescheduling', request=self._context, raise_exc=False
            )

            self._context.kwargs['rescheduled'] = True  # retry once
            self.task.apply_async(kwargs=self._context.kwargs, countdown=60)
            return

        self.task.simulate_failure(
            reason='hard_timeout',
            request=self._context, transition=transition,
            raise_exc=False, execute_hook=True,
        )
        # context.resource_w.timeout_hook(context.t.type)
