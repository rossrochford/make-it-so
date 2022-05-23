import json

from celery.contrib import rdb
from django_celery_results.models import TaskResult
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
import structlog

from resources.utils import CatchTime
from make_it_so.celery import IS_EAGER


logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class TransitionTaskContext:

    def __init__(
        self, transition, task_result_obj, request,
        cached_existing=None
    ):
        self.transition = transition
        self.task_result_obj = task_result_obj
        self.cached_existing = cached_existing

        self.model_obj = self.obj = transition.resource

        ResourceClass = self.obj.resource_class
        self.resource_w = ResourceClass(self.obj, transition)

        self.carrier = {}
        span_name = f'{request.id}_{request.retries}'
        with tracer.start_as_current_span(span_name, end_on_exit=False) as span:
            TraceContextTextMapPropagator().inject(self.carrier)

    @classmethod
    def fetch_transition(cls, transition_pk):
        from transitions.models import TransitionModel
        return TransitionModel.objects.select_related(
            'resource', 'resource__state_cause').filter(pk=transition_pk).first()

    @classmethod
    def fetch_task_result_object(cls, request):

        # task_id is sufficient but task_name has a DB index
        task_name = getattr(request, 'task', None)
        if task_name is None and IS_EAGER is False:
            logger.warning('request.task missing', id=request.id)

        filter_kwargs = {'task_id': request.id, 'task_name': task_name}
        if task_name is None:
            del filter_kwargs['task_name']

        task_result = TaskResult.objects.filter(**filter_kwargs).first()

        if task_result is None and IS_EAGER:  # hack for eager tasks
            task_result = TaskResult.objects.create(
                task_id=request.id,
                task_args=json.dumps(request.args),
                task_kwargs=json.dumps(request.kwargs)
            )

        return task_result

    @classmethod
    def populate_context(cls, request):
        with CatchTime() as t:
            tup = cls._populate_context(request)
        if t.duration > 0.5:
            logger.info('populate_context() duration', duration=t.duration)
        return tup

    @classmethod
    def _populate_context(cls, request):
        transition, task_result_obj = None, None
        transition_pk = request.kwargs.get('transition_pk')

        if transition_pk:
            transition = cls.fetch_transition(transition_pk)
            task_result_obj = cls.fetch_task_result_object(request)

        if transition is None or task_result_obj is None:
            logger.warning(
                'Transition or TaskResult missing', task_id=request.id,
                transition=transition, task_result_obj=task_result_obj
            )
            return False, None

        request.transition = transition

        cached_existing = None
        if request.retries == 0:
            cached_existing = request.kwargs.get('cached_existing')

        # additional context object, not to be confused with task.request
        # whose type is: celery.app.task.Context
        request.task_context = cls(
            transition, task_result_obj, request,
            cached_existing=cached_existing
        )

        return True, request.task_context

    @property
    def t(self):
        return self.transition

    def get_max_retries(self):
        retry_params = self.resource_w.get_retry_params(
            self.transition.type
        )
        return retry_params['max_retries']
