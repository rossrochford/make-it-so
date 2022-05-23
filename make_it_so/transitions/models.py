from ast import literal_eval
import os
import uuid

from django.db import models, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django_celery_results.models import TaskResult
from celery.contrib import rdb
from opentelemetry import trace
from structlog import getLogger

from base_classes.models import BaseModel
from make_it_so.celery import IS_EAGER
from transitions.celery_utils.exceptions import ensure_extra_info_is_serializable
from transitions.types import TransitionTypeEnum, TransitionStatusEnum


logger = getLogger(__name__)


CELERY_POOL_TYPE = os.environ['CELERY_POOL_TYPE']


EVENT_STATUS_SIDE_EFFECTS = {
    'sent_to_broker': 'sent_to_broker',
    'started': 'in_progress',
    'succeeded': 'succeeded',
    'terminal_failure': 'failed'
}


class TransitionModel(BaseModel):

    type = models.CharField(
        max_length=64, choices=TransitionTypeEnum.choices()
    )
    resource = models.ForeignKey(
        'resources.ResourceModel', on_delete=models.CASCADE
    )

    status = models.CharField(
        max_length=64, choices=TransitionStatusEnum.choices()
    )
    status_cause = models.ForeignKey(
        'TransitionEventModel', blank=True, null=True,
        on_delete=models.SET_NULL
    )

    update_type = models.CharField(  # used by ensure_update
        max_length=64, blank=True, null=True
    )
    extra_task_kwargs = models.JSONField(blank=True, null=True)

    previous_transition = models.ForeignKey(
        'TransitionModel', blank=True, null=True, on_delete=models.SET_NULL
    )
    # ideally this would be a FK on TaskResult, but it can't be customized
    celery_tasks = models.ManyToManyField(TaskResult, blank=True)

    @classmethod
    def create_transition(
        cls, resource_model, type, status='pending', prev=None
    ):
        assert TransitionTypeEnum.has_value(type)

        project = resource_model.project
        assert project is not None

        obj, created = TransitionModel.objects.get_or_create(
            type=type, status=status, resource=resource_model,
            defaults={'previous_transition': prev}
        )
        if created:
            rtype_short = resource_model.rtype.split('.')[-1]
            logger.info(
                f'new Transition: {type} on {rtype_short}', pk=obj.pk
            )
        return obj

    @property
    def resource_fullname(self):
        if self.resource is None:  # stop admin from breaking
            return 'RESOURCE MISSING'
        rslug = self.resource.slug
        rtype = self.resource.__class__.__name__
        return f'{rtype}.{rslug}'

    @property
    def task_started_at(self):
        latest_task = self.celery_tasks.all().order_by('date_created').last()
        if latest_task is None:
            return None
        return latest_task.date_created

    @classmethod
    def get_pending_and_inprogress(cls, resource_obj, exclude=None):
        query = TransitionModel.objects.filter(
            status__in=['pending', 'sent_to_broker', 'in_progress'],
            resource=resource_obj
        )
        if exclude:
            if not isinstance(exclude[0], int):
                exclude = [obj.id for obj in exclude]
            query = query.exclude(pk__in=exclude)
        return query

    def log_event(
        self, event_type, reason=None, info=None, extra_info=None
    ):
        # unlike ResourceModel.log_event() extra_info isn't expanded here
        info = info or extra_info
        if info:
            ensure_extra_info_is_serializable(info)

        self._print_event(event_type, reason, info)

        current_span = trace.get_current_span()
        if current_span._context.span_id == 0:
            if event_type != 'sent_to_broker':
                logger.info('no span for event', type=event_type)
        else:
            current_span.add_event(event_type)

        event = TransitionEventModel.objects.create(
            type=event_type, reason=reason, transition=self, extra_info=info
        )

        next_status = EVENT_STATUS_SIDE_EFFECTS.get(event_type)
        if next_status == self.status:
            next_status = None

        if next_status:
            self.status = next_status
            self.status_cause = event
            self.save()

    def _print_event(self, event_type, reason=None, extra_info=None):
        msg = f'[TRANSITION-EVENT: {event_type}] on: Transition {self.pk}'
        log_kwargs = dict(event=msg)
        if reason:
            log_kwargs['reason'] = reason
        if extra_info:
            log_kwargs['extra_info'] = extra_info

        log_func = logger.info
        if event_type in ('error', 'terminal_failure'):
            log_func = logger.warning

        log_func(**log_kwargs)

    def celery_apply_async(self, app):
        from transitions.tasks import TASK_SIGNATURES_BY_TRANSITION_TYPE

        if self.status != 'pending' and IS_EAGER is False:
            logger.warning(
                'executing task Transition that is not pending',
                pk=self.pk, status=self.status
            )
        type_key = self.type
        # if self.update_type:
        #     type_key = (self.type, self.update_type)
        task_signature = app.signature(
            TASK_SIGNATURES_BY_TRANSITION_TYPE[type_key]
        )
        task_kwargs = {'transition_pk': self.pk}
        if self.extra_task_kwargs:
            task_kwargs.update(self.extra_task_kwargs)

        ResourceClass = self.resource.resource_class
        assert ResourceClass is not None
        time_params = ResourceClass.get_retry_params(self.type)

        apply_kwargs = {'kwargs': task_kwargs}
        if 'soft_time_limit' in time_params:
            apply_kwargs['soft_time_limit'] = time_params['soft_time_limit']
            if CELERY_POOL_TYPE == 'gevent':
                apply_kwargs['time_limit'] = time_params['soft_time_limit']
        if 'time_limit' in time_params:
            apply_kwargs['time_limit'] = time_params['time_limit']

        self.log_event('sent_to_broker')

        return task_signature.apply_async(**apply_kwargs)

    def __str__(self):
        return f'TransitionModel: {self.pk}'


class TransitionEventModel(BaseModel):

    type = models.CharField(
        max_length=64, # choices=[]
    )
    reason = models.TextField(blank=True, null=True)

    status_decision = models.CharField(
        max_length=64, blank=True, null=True
    )
    extra_info = models.JSONField(
        blank=True, null=True, default=dict
    )
    transition = models.ForeignKey(
        'transitions.TransitionModel', blank=True, null=True,
        on_delete=models.CASCADE
    )

    def __str__(self):
        return f'"{self.type}" event on: {self.transition}'


@receiver(post_save, sender=TaskResult)
def ensure_transition_marked_as_failed(sender, instance: TaskResult, **kwargs):
    """
        Exceptions raised by internally by TransitionTask sometimes fail to
        trigger on_failure(), this signal ensures Transitions are marked as 'failed'

        # note: another option is to use celery's task_failure_notifier
    """
    if kwargs['created'] or instance.status != 'FAILURE':
        return

    transition = _fetch_transition_for_taskresult(instance)
    if transition and transition.status != 'failed':
        transition.log_event('terminal_failure')


def _fetch_transition_for_taskresult(task_result):

    transition_pk = None

    if task_result.task_kwargs:
        try:
            task_kwargs = literal_eval(task_result.task_kwargs.strip('"'))
            transition_pk = task_kwargs.get('transition_pk')
        except (Exception, SyntaxError):
            logger.info('failed to parse task_kwargs', pk=task_result.pk)

    if transition_pk:
        transition = TransitionModel.objects.filter(pk=transition_pk).first()
    else:
        transition = TransitionModel.objects.filter(
            celery_tasks__in=[task_result]
        ).first()

    if transition is None:
        logger.warning(
            'failed to fetch Transition', task_result=task_result.id
        )
    return transition
