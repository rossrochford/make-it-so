from typing import Dict

import gevent
import structlog

from resources.models import ResourceModel
from resources.utils import ResourceApiListResponse, CatchTime
from transitions.celery_utils import get_exponential_backoff_interval


logger = structlog.get_logger(__name__)


'''
todo: implement resource class validation steps:
    - if PROVIDER_ID_FIELD != 'slug' 
        - ensure that it has an impl for generate_provider_id()
        - ensure provider field name is on either the BASE_MODEL or EXTRA_FIELDS_MODEL_CLASS
        - ensure provider field is not optional
    - ensure EXTRA_FIELDS_MODEL_CLASS is set
    - ensure pydantic model's field names don't conflict with the base model
    - validate retry args (currently we're doing this on every retry but we only need to do it once)
'''

# future work:
'''
- add Provider class, this contains:
    - an EXTRA_FIELDS_MODEL_CLASS for ProjectModel
    - a base pydantic model for resources (and if it is set, we hook this into the validation on the resource classes)
    - the create_cli() method
'''

RETRY_PARAMS = {
    # summary of params: http://www.ines-panker.com/2020/10/29/retry-celery-tasks.html
    'test': {
        # 'default_retry_delay': 6,
        'retry_backoff': 2,
        'max_retries': 5,
        'total_timeout': 300
    },
    'ensure_dependencies_ready': {
        'default_retry_delay': 15,
        'max_retries': 5
    },
    'ensure_exists': {
        'default_retry_delay': 15,
        'max_retries': 5
    },
    'ensure_healthy': {
        'default_retry_delay': 15,
        'max_retries': 6
    },
    'ensure_updated': {
        'default_retry_delay': 15,
        'max_retries': 3
    },
    'ensure_forward_dependencies_deleted': {
        'default_retry_delay': 15,
        'max_retries': 5
    },
    'ensure_deleted': {
        'default_retry_delay': 15,
        'max_retries': 5
    }
}


class ProviderBase:

    def __init__(self):
        pass

    @classmethod
    def create_cli(cls, rtype, project):
        raise NotImplementedError


class ResourceBase:

    RESOURCE_MODEL_CLASS = ResourceModel
    EXTRA_FIELDS_MODEL_CLASS = None

    HAS_DEPENDENCIES = True  # true by default, most resources have dependencies

    PROVIDER = None
    PROVIDER_ID_FIELD = 'slug'

    FETCH_DELAY = 3

    # AUTORETRY_FOR = None  not trivial to set this per resource
    RETRY_PARAMS = RETRY_PARAMS

    def __init__(self, model_obj, transition, cli=None):
        self.cluster = None  # disabled for now
        self.project = model_obj.project

        self.t = transition  # note: this is None when ingesting HCL
        self.transition = transition

        self.cli = cli
        if self.cli is None:
            with CatchTime() as t:
                self.cli = self.create_cli(model_obj.rtype, self.project)
            if t.duration > 0.25:
                logger.info('create_cli()', duration=t.duration)

        self.model_obj = model_obj
        model_obj.extra_fields_model_class = self.EXTRA_FIELDS_MODEL_CLASS

        self.labels = {}
        if self.cluster:
            self.labels = {'cluster_uid': self.cluster.uid}

    def get_provider_identifier(self):
        field_name = self.PROVIDER_ID_FIELD

        if field_name in ('slug', 'id'):
            return getattr(self.model_obj, field_name)

        if self.model_obj.extra_data is None:
            self.model_obj.extra_data = {}  # unlikely, but just in case

        stored_id = self.model_obj.extra_data.get(field_name)
        if stored_id:
            return stored_id

        logger.warning(  # should have been set during hcl ingestion
            f'provider id {field_name} was not found in extra_data'
        )
        generated_id = self.generate_provider_id(self.model_obj)
        self.model_obj.extra_data[field_name] = generated_id
        self.model_obj.save()
        return generated_id

    @classmethod
    def get_initial_transition_type(cls):
        if cls.HAS_DEPENDENCIES:
            return 'ensure_dependencies_ready'
        return 'ensure_exists'

    @staticmethod
    def generate_provider_id(model_obj):
        raise Exception('generate_provider_id() is not implemented')

    @classmethod
    def get_resource(cls, cli, project, provider_id):
        resources_by_id = cls.list_resources(cli, project)
        return resources_by_id.get(provider_id)

    def fetch(self):
        return self.get_resource(
            self.cli, self.project, self.get_provider_identifier()
        )

    @classmethod
    def list_resources(cls, cli, project) -> Dict[str, ResourceApiListResponse]:
        raise NotImplementedError

    def create_resource(self):
        raise NotImplementedError

    def delete_resource(self):
        raise NotImplementedError

    @classmethod
    def create_cli(cls, rtype, project):
        return cls.PROVIDER.create_cli(rtype, project)

    @classmethod
    def clean(cls, model_obj):
        pass

    def check_exists(self, num_retries=1, cached_existing=None):
        assert num_retries > 0

        for i in range(num_retries):

            existing_by_id = cached_existing if i == 0 else None
            if existing_by_id is None:
                existing_by_id = self.list_resources(self.cli, self.project)

            id = self.get_provider_identifier()
            if id in existing_by_id:
                return True, existing_by_id[id]
            gevent.sleep(self.FETCH_DELAY)

        return False, None

    def exists_hook(
        self, creation_response=None, list_response=None,
        provider_id=None
    ):
        logger.warning(
            'exists_hook() implementation missing', cls=self.__class__
        )

    def healthy_hook(self):
        pass

    @property
    def health_checks(self):
        return [
            getattr(self, a) for a in dir(self)
            if a.startswith('health_check__')
        ]

    def do_update(self):
        if self.t.update_type:
            method = getattr(self, f'do_update__{self.t.update_type}')
            return method()

    def timeout_hook(self, transition_type):  # no longer used
        logger.info('timeout_hook() called', type=transition_type)

        if transition_type == 'ensure_healthy':
            self.model_obj.log_event(
                'unhealthy', 'ensure_healthy Transition timed out'
            )
            return

    def deleted_hook(self):
        pass

    @classmethod
    def _validate_retry_params(cls, params, trans_type):
        log_args = dict(transition_type=trans_type, resource=cls.__name__)
        if 'max_retries' not in params:
            logger.warning(f'max_retries arg missing', **log_args)
            return False
        if not ('retry_backoff' in params or 'default_retry_delay' in params):
            logger.warning(
                'retry_backoff or default_retry_delay is missing', **log_args
            )
            return False
        if 'retry_backoff_max' in params and params['retry_backoff_max'] < 1:
            logger.warning('retry_backoff_max must be >= 1', **log_args)
            return False
        return True

    @classmethod
    def get_retry_params(cls, transition_type):
        if transition_type not in cls.RETRY_PARAMS:
            # skipping validation
            return ResourceBase.RETRY_PARAMS[transition_type]

        params = cls.RETRY_PARAMS[transition_type]
        if cls._validate_retry_params(params, transition_type):
            return params

        # validation failed, falling back to defaults
        return ResourceBase.RETRY_PARAMS[transition_type]

    def get_next_retry_countdown(
        self, retry_index, transition_type, task_age=None
    ):
        params = self.get_retry_params(transition_type)

        if retry_index >= params['max_retries']-1:
            return None, 'retries_exhausted'

        if 'total_timeout' in params and task_age:
            if task_age > params['total_timeout']:
                return None, 'total_timeout_exceeded'

        if 'retry_backoff' in params:
            countdown = get_exponential_backoff_interval(
                params['retry_backoff'],
                retry_index,
                0.5,  # min
                params.get('retry_backoff_max', 300),
                params.get('retry_jitter', False)
            )
        else:
            countdown = params['default_retry_delay']

        return countdown, None
