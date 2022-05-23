from celery.signals import task_received, task_prerun, task_success, task_failure


@task_received.connect
def task_received_handler(sender=None, headers=None, body=None, **kwargs):
    print(f'received_handler')


@task_prerun.connect
def task_prerun_handler(task_id, task, **kwargs):  # before task is about to be run
    print(f'prerun_handler')


@task_success.connect
def task_success_notifier(sender=None, **kwargs):
    task_id = kwargs.get('task_id')
    print(f'task_success_notifier(): {task_id}')


@task_failure.connect
def task_failure_notifier(sender=None, **kwargs):
    task_id = kwargs.get('task_id')
    print(f'task_failure_notifier(): {task_id}')
