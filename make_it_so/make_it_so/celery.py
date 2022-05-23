import datetime
import os

from celery.contrib import rdb
from celery import Celery, bootsteps
from celery.signals import worker_process_init, worker_init
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry import trace, context
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


# based on: https://testdriven.io/courses/django-celery/getting-started/
# and: https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html

# some other ways to configure celery: https://betterprogramming.pub/python-celery-best-practices-ae182730bb81


# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'make_it_so.settings')


CELERY_POOL_TYPE = os.environ['CELERY_POOL_TYPE']
assert CELERY_POOL_TYPE in ('prefork', 'gevent')
# others: 'eventlet', 'solo', 'processes' ('processes' is just an alias of prefork)


'''
class CustomCelery(Celery):

    # 'on_after_finalize', 'on_after_fork', 'on_configure', 'on_init'
    def on_configure(self, *args, **kwargs):
        rdb.set_trace()
        return super().on_configure(*args, **kwargs)
'''

worker_signal = worker_process_init if CELERY_POOL_TYPE == 'prefork' else worker_init


@worker_signal.connect(weak=False)
def init_celery_tracing(*args, **kwargs):
    CeleryInstrumentor().instrument()
    return

    resource = Resource.create({SERVICE_NAME: "celery-worky"})
    trace_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(trace_provider)
    CeleryInstrumentor().instrument()

    tracer = trace.get_tracer("MyCeleryApp")

    with tracer.start_as_current_span('initializing-celery-telemetry'):
        exporter = JaegerExporter(
            agent_host_name="localhost", agent_port=6831,
        )
        span_processor = SimpleSpanProcessor(exporter)
        trace.get_tracer_provider().add_span_processor(span_processor)


app = Celery('proj')

STORE_RESULTS = True
IS_EAGER = os.environ.get('CELERY_TASK_ALWAYS_EAGER', 'false').lower() == 'true'


class Config:

    # timezone config
    enable_utc = True
    timezone = 'UTC'  # 'Europe/London'

    # task reliability configs
    # note: task_acks_late=True improves reliability but your tasks
    # must be idempotent and visibility_timeout > max task duration
    # see: https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html#redis-caveats
    # https://docs.celeryq.dev/en/stable/faq.html#should-i-use-retry-or-acks-late
    task_track_started = True
    task_soft_time_limit = 60 * 60  # 1h, global soft limit
    task_time_limit = 62 * 60  # global hard limit
    task_acks_late = True  # can be set on task-level, e.g. if not idempotent
    task_acks_on_failure_or_timeout = True
    task_reject_on_worker_lost = True

    # broker configs
    broker_url = 'redis://localhost:6379/0'
    redis_retry_on_timeout = True
    broker_pool_limit = 20  # max connections to broker, increase with num workers
    broker_transport_options = {
        # important: ensure this exceeds the longest task (or soft limit)
        'visibility_timeout': 180 * 60  # 3h
    }

    # celery result/cache backend settings
    # based on: https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html#django-celery-results
    cache_backend = 'django_redis_cache'
    result_backend = 'django-db'
    result_extended = True
    ignore_result = False
    result_backend_always_retry = True
    result_backend_base_sleep_between_retries_ms = 80
    result_expires = datetime.timedelta(days=1)

    # performance/scaling configs
    # lower prefetch is better for long-running tasks, see: https://docs.celeryq.dev/en/stable/userguide/optimizing.html#prefetch-limits
    worker_prefetch_multiplier = 2

    # retry behaviour on the client side
    task_publish_retry = True
    task_publish_retry_policy = {
        'max_retries': 10,
        'interval_start': 0.3,
        'interval_step': 0.5,
        'interval_max': 10,
    }

    # logging, monitoring and exception handling
    task_remote_tracebacks = False  # true requires tblib library
    worker_send_task_events = False  # should be True when using Flower
    task_send_sent_event = False

    # periodic tasks
    beat_schedule = {
        'create-transitions-for-healthy': {
            'task': 'transitions.tasks.daemon_tasks.create_missing_transitions',
            'schedule': 10
        },
        'submit-transition-tasks': {
            'task': 'transitions.tasks.daemon_tasks.submit_transition_tasks',
            'schedule': 12
        }
    }


app.config_from_object(Config)
# app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()
app.autodiscover_tasks([
    'resources.tasks.hcl_ingest_models',
    'resources.tasks.hcl_express_desired_state',
    'transitions.tasks.daemon_tasks.create_missing_transitions',
    'transitions.tasks.daemon_tasks.submit_transition_tasks',
    'transitions.tasks.ensure_dependencies_ready',
    'transitions.tasks.ensure_exists',
    'transitions.tasks.ensure_healthy',
    'transitions.tasks.ensure_updated',
    'transitions.tasks.ensure_deleted',
    'transitions.tasks.test_task',
])


if not STORE_RESULTS:
    app.conf.update(
        task_ignore_result=True,
        task_store_errors_even_if_ignored=True
    )

if IS_EAGER:
    app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        task_store_eager_result=True
    )


class StorePoolTypeOnCeleryApp(bootsteps.Step):

    def __init__(self, parent, **options):
        super().__init__(parent, **options)  # also: pool_cls=options['pool']
        parent.app.pool_type = str(parent.pool_cls).split('.')[2]
        parent.app.pool_is_green = parent.pool_cls.is_green

        assert CELERY_POOL_TYPE == parent.app.pool_type

        # from transitions.tasks.test_task import test_task


app.steps['worker'].add(StorePoolTypeOnCeleryApp)




# @worker_process_init.connect(weak=False)
# def init_celery_tracing(*args, **kwargs):
#     CeleryInstrumentor().instrument()



'''
# alternative to bootsteap approach:

@celeryd_init.connect
def init_worker(conf=None, **kwargs):
    pool_cls = kwargs['options']['pool']
'''


# celery-structlog integration:
# https://django-structlog.readthedocs.io/en/latest/celery.html#getting-started-with-celery

# @app.task(bind=True)
# def debug_task(self):
#     print(f'Request: {self.request!r}')

# https://devchecklists.com/celery-tasks-checklist/

# cel actor framework:
# https://cell.readthedocs.io/en/latest/introduction.html
# https://cell.readthedocs.io/en/latest/getting-started/index.html#getting-started



# starting a worker:  (statedb persists worker state between restarts, for example its internal list of revoked tasks)
#  $  celery -A make_it_so worker --pool=gevent --concurrency=10 --statedb=./worker_state.db

# starting celery.beat:
#  $  celery -A make_it_so beat
