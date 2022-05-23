from transitions.tasks.ensure_dependencies_ready import ensure_dependencies_ready
from transitions.tasks.ensure_forward_dependencies_deleted import ensure_forward_dependencies_deleted
from transitions.tasks.ensure_deleted import ensure_deleted
from transitions.tasks.ensure_exists import ensure_exists
from transitions.tasks.ensure_healthy import ensure_healthy
from transitions.tasks.ensure_updated import ensure_updated
from transitions.tasks.test_task import test_task


_tasks = [
    ensure_dependencies_ready,
    ensure_exists,
    ensure_healthy,
    ensure_updated,
    ensure_forward_dependencies_deleted,
    ensure_deleted,
    test_task
]

TASK_SIGNATURES_BY_TRANSITION_TYPE = {
    t.__name__: t.name for t in _tasks
}

TASKS_BY_TRANSITION_TYPE = {
    t.__name__: t for t in _tasks
}

# TASK_SIGNATURES_BY_TRANSITION_TYPE = {
#     'ensure_dependencies_ready': 'transitions.tasks.ensure_dependencies_ready.ensure_dependencies_ready',
#     'ensure_exists': 'transitions.tasks.ensure_exists.ensure_exists',
#     'ensure_updated': 'transitions.tasks.ensure_updated.ensure_updated',
#     'ensure_healthy': 'transitions.tasks.ensure_healthy.ensure_healthy',
#     'test': 'transitions.tasks.test_task.test_task',  # 'state_machine.tasks.test_task2',
# }

