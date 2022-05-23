from collections import defaultdict
import re
from typing import Union

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import UniqueConstraint
from django.db.models import Q
from django.utils import timezone
from celery.contrib import rdb
from opentelemetry import trace
from shortuuid.django_fields import ShortUUIDField
import structlog

from base_classes.enum_types import BaseStrEnum
from base_classes.models import BaseModel
from resources import (
    get_resource_classes, decide_next_state_from_event, log_activity_on_resource
)
from resources.managers import ResourceModelManager
from resources.types import (
    DesiredStateEnum, ResourceStateEnum,
    ExistenceEnum, HealthEnum, ResourceEventTypeEnum
)
from transitions.celery_utils.exceptions import create_extra_info
from transitions.models import TransitionModel


logger = structlog.get_logger(__name__)


GCP_ID_REGEX = r'^[a-z][-a-z0-9]{0,61}[a-z0-9]$'  # todo: use cloudbridge's standard


def validate_slug(value):
    if len(value) > 47:
        raise ValidationError(f'slug length too long: {len(value)}')
    match = re.search(GCP_ID_REGEX, value, flags=re.I)
    if match is None:
        raise ValidationError(f'invalid slug: {value}')
    return value


def validate_rtype(value):
    resource_classes = ResourceModel.get_resource_classes()

    if value not in resource_classes:
        raise ValidationError(f'unexpected rtype: {value}')

    return value


class ResourceEventModel(BaseModel):

    type = models.CharField(  # todo: TEMP choices commented out
        max_length=64, #choices=ResourceEventTypeEnum.choices()
    )
    reason = models.TextField(blank=True, null=True)

    state_decision = models.CharField(
        max_length=64, blank=True, null=True
    )
    extra_info = models.JSONField(
        blank=True, null=True, default=dict
    )

    resource = models.ForeignKey('ResourceModel', on_delete=models.CASCADE)
    transition = models.ForeignKey(
        'transitions.TransitionModel', blank=True, null=True,
        on_delete=models.SET_NULL
    )

    def __str__(self):
        return f'"{self.type}" event on: {self.resource}'


class ResourceDependencyModel(BaseModel):

    resource = models.ForeignKey(
        'ResourceModel', related_name='forward_rels',
        on_delete=models.CASCADE
    )
    depends_on = models.ForeignKey(
        'ResourceModel', related_name='backward_rels',
        on_delete=models.CASCADE
    )
    field_name = models.CharField(max_length=64)

    class Meta:
        unique_together = ('resource', 'depends_on', 'field_name')

    def __str__(self):
        _, resource_class_name = self.resource.rtype.split('.')
        resource_type_name = resource_class_name.rstrip('Resource')

        _, dependency_class_name = self.depends_on.rtype.split('.')
        dependency_type_name = dependency_class_name.rstrip('Resource')

        return f'{resource_type_name}:{self.resource_id}.{self.field_name} -> {dependency_type_name}:{self.depends_on_id}'


class ResourceModel(BaseModel):

    RESOURCE_CLASSES = None
    RELATED_FIELDS = ['project']

    def __init__(self, *args, **kwargs):
        resource_class = kwargs.pop('resource_class', None)
        super().__init__(*args, **kwargs)
        self._resource_class = resource_class
        self._extra_attrdict = None

    # note: unlike a regular integer pk, this gets set when instantiating
    # an object (i.e. before save() is called) so you can't check use self.pk
    # to ascertain whether an instance is new. This doesn't appear to be a bug,
    # django's built-in models.UUIDField() does the same.
    id = ShortUUIDField(
        length=16, max_length=16, primary_key=True, editable=False,
        alphabet='123456789abcdefghijklmnopqrstuvwxyz'
    )
    slug = models.CharField(
        max_length=47, validators=[validate_slug]
    )
    hcl_slug = models.CharField(
        max_length=255, blank=True, null=True, unique=False
    )
    rtype = models.CharField(
        max_length=255, validators=[validate_rtype]
    )

    project = models.ForeignKey(
        'users.ProjectModel', on_delete=models.CASCADE
    )

    labels = models.JSONField(default=dict, blank=True)

    creation_response = models.JSONField(blank=True, null=True)
    list_response = models.JSONField(blank=True, null=True)
    getter_response = models.JSONField(blank=True, null=True)

    extra_data = models.JSONField(blank=True, null=True)

    # outcome fields:
    # --------------------------------
    desired_state = models.CharField(
        max_length=64, choices=DesiredStateEnum.choices(),
        blank=True, null=True
    )
    state = models.CharField(
        max_length=64, choices=ResourceStateEnum.choices(),
        default=ResourceStateEnum.newborn_model
    )
    state_cause = models.ForeignKey(  # event that caused state change
        'ResourceEventModel', blank=True, null=True,
        on_delete=models.SET_NULL
    )

    # 'existence' and 'health' are more recent/fine-grained than 'state'.
    # The system may update these incidentally as side effects, so as not to
    # waste data that may be useful for monitoring or anomaly detection.
    # (could also include fields to record dependency checks?)
    existence = models.CharField(
        max_length=64, choices=ExistenceEnum.choices(),
        default=ExistenceEnum.unknown
    )
    existence_last_checked_at = models.DateTimeField(blank=True, null=True)
    resource_created_at = models.DateTimeField(blank=True, null=True)
    health = models.CharField(  # is the resource healthy?
        max_length=64, choices=HealthEnum.choices(),
        default=HealthEnum.unknown
    )
    health_last_checked_at = models.DateTimeField(blank=True, null=True)
    # --------------------------------

    objects = ResourceModelManager()

    class Meta:
        constraints = [
            UniqueConstraint(
                name='rtype_slug_is_unique_per_project',
                fields=('slug', 'rtype', 'project')
            ),
            UniqueConstraint(
                name='hcl_slug_is_unique_per_project',
                fields=('hcl_slug', 'project'),
                condition=~(Q(hcl_slug=None) | Q(hcl_slug=''))
                # condition=Q(hcl_slug__isnull=False)
            )
        ]

    @classmethod
    def get_resource_classes(cls):
        if cls.RESOURCE_CLASSES is None:
            cls.RESOURCE_CLASSES = get_resource_classes()
        return cls.RESOURCE_CLASSES

    @classmethod
    def get_related_fields(cls):
        return cls.RELATED_FIELDS

    def __str__(self):
        cls_name = self.__class__.__name__
        app_name, resource_class_name = self.rtype.split('.')
        resource_type_name = resource_class_name.rstrip('Resource')
        return f'{cls_name}:{resource_type_name}:{self.pk}:{self.slug}'

    @property
    def x(self):  # shorter alias
        return self.extra

    @property
    def extra(self):
        
        if self._extra_attrdict:
            return self._extra_attrdict
        
        if self.extra_data is None:
            logger.error('ResourceModel.extra called when extra_data is None')
            return None

        ExtraModelClass = self.resource_class.EXTRA_FIELDS_MODEL_CLASS
        pydantic_obj = ExtraModelClass(**self.extra_data)

        self._extra_attrdict = pydantic_obj.create_attr_dict()
        return self._extra_attrdict

    def get_dependencies(self):
        query = ResourceDependencyModel.objects.select_related('depends_on').filter(
            resource=self.id
        )

        dependencies_by_field = defaultdict(list)
        for rel in query:
            dependencies_by_field[rel.field_name].append(rel.depends_on)

        # overwrite lists and warn about duplicates
        for field_name, dependencies in dependencies_by_field.items():
            if len(dependencies) > 1:
                logger.warning(
                    'multiple dependencies found for field',
                    resource=self.id, field_name=field_name
                )
            dependencies_by_field[field_name] = dependencies[-1]

        return dict(dependencies_by_field)

    def get_forward_dependencies(self):
        query = ResourceDependencyModel.objects.select_related('resource').filter(
            depends_on=self.id
        )
        return [obj.resource for obj in query]

    @property
    def resource_age(self):
        if self.resource_created_at is None:
            return None
        return (timezone.now() - self.resource_created_at).total_seconds()

    @property
    def resource_class(self):
        if self.RESOURCE_CLASSES is None:
            self.RESOURCE_CLASSES = get_resource_classes()
        if self._resource_class is None:
            self._resource_class = self.RESOURCE_CLASSES[self.rtype]
        return self._resource_class

    def get_transition_history(self, reverse=False, status=None, statuses=None):

        order_by = '-created' if reverse else 'created'
        filter_kwargs = dict(resource=self)

        assert not (status and statuses)  # both should never be set
        if statuses:
            filter_kwargs['status__in'] = statuses
        if status:
            filter_kwargs['status'] = status

        query = TransitionModel.objects.filter(
            **filter_kwargs).order_by(order_by).all()
        
        return [o for o in query]

    def get_event_history(self, reverse=False, slugs=False):

        order_by = '-created' if reverse else 'created'
        filter_kwargs = dict(resource=self)

        query = ResourceEventModel.objects.filter(
            **filter_kwargs).order_by(order_by).all()

        events = [o for o in query]
        if slugs:
            return [e.type for e in events]
        return events

    def get_state_history(self, reverse=False):
        order_by = '-created' if reverse else 'created'
        query = ResourceEventModel.objects.filter(
            reousrce=self, state_decision__isnull=False
        ).order_by(order_by).all()
        return [e.state_decision for e in query]

    def log_event(
        self, event_type: Union[BaseStrEnum, str], reason=None,
        transition=None, t=None, extra_info=None, info=None,
        exc=None, einfo=None, celery_task=None, next_state=None
    ):
        transition = transition or t
        extra_info = extra_info or info

        if not ResourceEventTypeEnum.has_value(event_type):
            pass #logger.warning(f'Unexpected event_type: "{event_type}" on {self}')
            # raise ValueError(f'Unexpected event_type: "{event_type}" on {self}') TEMP commenting out

        extra_info = create_extra_info(
            self, exc=exc, einfo=einfo, extra_info=extra_info,
            celery_task=celery_task
        )

        current_span = trace.get_current_span()
        if current_span._context.span_id == 0:
            logger.info('no span for event', type=event_type)
        else:
            current_span.add_event(event_type)

        self._print_event(event_type, reason, extra_info)

        # note: we will also want to trigger 'resource_found' without a next_state side effect,
        # this would be when fetch_existing() discovers resources outside the current Transition

        log_activity_on_resource(self, event_type)

        if next_state is None and transition is None:
            logger.warning(
                'log_event() got no Transition', etype=event_type, resource=str(self)
            )
        if transition is None and event_type == 'terminal_failure':
            logger.warning(  # how to determine this is creation, update or delete?
                'terminal_failure event logged without Transition, '
                'this may cause infinite loops'
            )

        if next_state is None and transition:
            next_state = decide_next_state_from_event(
                transition.type, event_type, reason
            )

        if next_state == self.state:
            next_state = None

        event_obj = ResourceEventModel.objects.create(
            type=event_type, reason=reason, resource=self,
            transition=transition, extra_info=extra_info,
            state_decision=next_state
        )

        if next_state:
            rtype = self.rtype.split('.')[-1]
            logger.info('updating state', rtype=rtype, state=next_state)
            self.state = next_state
            self.state_cause = event_obj

        self.save()

    def _print_event(self, event_type, reason=None, extra_info=None):
        rtype = self.rtype.split('.')[-1]
        log_kwargs = dict(event=f'[RESOURCE-EVENT: {event_type}] on: {rtype}')
        if reason:
            log_kwargs['reason'] = reason
        if extra_info:
            extra_info = extra_info.copy()
            extra_info.pop('current_state', None)
            if extra_info:
                log_kwargs['extra_info'] = extra_info

        log_func = logger.info
        if event_type in ('error', 'terminal_failure'):
            log_func = logger.warning

        log_func(**log_kwargs)

    def get_output(self, attr_name):
        return getattr(self, attr_name)


'''
@receiver(post_save, sender=ResourceModel)
def set_provider_id(sender, instance, **kwargs):
    logger.info(
        'set_provider_id() called', cls=instance.resource_class
    )


def save(self, *args, **kwargs):
    if not self.self_link:
        self.self_link = self.generate_provider_id()
    super().save(*args, **kwargs)
'''