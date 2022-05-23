import os
from os.path import join as join_path
import time

from django.core.management.base import BaseCommand
import structlog

# connection to broker seems to only work when this is imported
from make_it_so.celery import app

from resources.tasks import _hcl_ingest_models, _hcl_express_desired_state
from resources.hcl_utils.ingestion import fetch_project_from_provider_block
from resources.hcl_utils.parsing import parse_hcl_file


logger = structlog.get_logger(__name__)


def _read_file(filepath):

    if not os.path.exists(filepath):
        exit(f'file not found: {filepath}')

    with open(filepath) as f:
        hcl_file_content = f.read()

    return hcl_file_content


class Command(BaseCommand):

    def add_arguments(self, parser):
        parser.add_argument('filepath', type=str)
        parser.add_argument('desired_state', nargs='?', default='healthy')

    def handle(self, *args, **kwargs):

        hcl_file_content = _read_file(kwargs['filepath'])
        desired_state = kwargs['desired_state']
        assert desired_state in ('healthy', 'deleted')

        locals, ordered_entries, entries_by_name, provider_block = parse_hcl_file(
            file_content=hcl_file_content
        )
        if ordered_entries is None:
            exit('failed to parse hcl file')

        project = fetch_project_from_provider_block(provider_block)
        if project is None:
            raise Exception('project not found')

        _hcl_ingest_models(project.pk, hcl_file_content)
        _hcl_express_desired_state(
            project.pk, hcl_file_content, desired_state
        )

        # from make_it_so.celery import app
        # task_signature = app.signature('resources.tasks.apply_hcl')
        # task_signature.apply_async(kwargs=task_kwargs)



'''
# for testing broker connection issues:

def sent_test():
    from kombu import Connection
    connection = Connection('redis://localhost:6379/0')
    send_as_task(
        connection, fun=app.signature('transitions.tasks.test_task.test_task'),
        args=tuple(), kwargs={'transition_pk': 33}
    )


def send_as_task(connection, fun, args=(), kwargs={}):
    from kombu.pools import producers

    payload = {'fun': fun, 'args': args, 'kwargs': kwargs}
    with producers[connection].acquire(block=True) as producer:
        producer.publish(payload,
                         serializer='json',
                         routing_key='celery',
                         exchange='celery'
        )
'''

'''
# simulating worker in a single thread:

for i in range(40):

    create_missing_transitions.apply_async()

    excl = ['succeeded', 'failed']  # re-submit in-progress Transitions
    for t in TransitionModel.objects.exclude(status__in=excl):
        task = TASKS_BY_TRANSITION_TYPE[t.type]
        apply_kwargs = {'kwargs': {'transition_pk': t.pk}}
        try:
            task.apply_async(**apply_kwargs)
        except (TaskRetryException, InternalRetryException, TaskFailureException):
            pass

    time.sleep(6)
'''