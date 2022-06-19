from typing import Dict, List

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


class ResourceIdentifier:

    MODEL_FIELD = None

    def __init__(self):
        pass

    @staticmethod
    def generate(resource_model):
        raise NotImplementedError

    @staticmethod
    def get_id_from_list_response(list_resp):
        raise NotImplementedError

    @staticmethod
    def get_id_from_creation_response(creation_resp):
        return None  # optional

    @classmethod
    def fetch_id(cls, resource_model):
        assert cls.MODEL_FIELD is not None
        field_name = cls.MODEL_FIELD

        if field_name in ('slug', 'id'):
            return getattr(resource_model, field_name)

        extra_data = resource_model.extra_data or {}

        stored_id = extra_data.get(field_name)
        if stored_id:
            return stored_id

        return cls.generate(resource_model)


class ResourceBase:

    RESOURCE_MODEL_CLASS = ResourceModel
    EXTRA_FIELDS_MODEL_CLASS = None
    IDENTIFIER = None

    HAS_DEPENDENCIES = True  # true by default, most resources have dependencies

    PROVIDER = None

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

    @classmethod
    def get_initial_transition_type(cls):
        if cls.HAS_DEPENDENCIES:
            return 'ensure_dependencies_ready'
        return 'ensure_exists'

    @classmethod
    def get_resource(cls, cli, project, id):
        existing = {
            cls.IDENTIFIER.get_id_from_list_response(resp): resp
            for resp in cls.list_resources(cli, project)
        }
        return existing.get(id)

    def fetch(self):
        id = self.IDENTIFIER.fetch_id(self.model_obj)
        return self.get_resource(self.cli, self.project, id)

    @classmethod
    def list_resources(cls, cli, project) -> List:
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
            exists, resp = self._do_check(i, cached_existing)
            if exists:
                return True, resp
            gevent.sleep(self.FETCH_DELAY)

        return False, None

    def _do_check(self, i, cached_existing):
        existing = cached_existing if i == 0 else None
        if existing is None:
            existing = {
                self.IDENTIFIER.get_id_from_list_response(resp): resp
                for resp in self.list_resources(self.cli, self.project)
            }
        id = self.IDENTIFIER.fetch_id(self.model_obj)
        exists = id in existing
        return exists, existing.get(id)

    def exists_hook_base(
        self, creation_response=None, list_response=None
    ):
        obj = self.model_obj

        assert self.IDENTIFIER is not None and self.IDENTIFIER.MODEL_FIELD is not None
        assert creation_response or list_response
        assert not (creation_response and list_response)

        id_field_name = self.IDENTIFIER.MODEL_FIELD

        if obj.extra_data is None:  # just in case
            obj.extra_data = {}
            obj.save()

        id = None
        if id_field_name not in ('slug', 'id'):
            if creation_response:
                id = self.IDENTIFIER.get_id_from_creation_response(creation_response)
            if list_response:
                id = self.IDENTIFIER.get_id_from_list_response(list_response)

            if id is not None and id != obj.extra_data.get(id_field_name):
                obj.extra_data[id_field_name] = id
                obj.save()

    def exists_hook(
        self, creation_response=None, list_response=None
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
