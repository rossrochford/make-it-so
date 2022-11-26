from base_classes.enum_types import BaseStrEnum


class ProviderTypesEnum(BaseStrEnum):
    HETZNER = 'hetzner'
    GOOGLE = 'google'


'''
class ResourceModelStateEnum(BaseStrEnum):
    # transition 1
    dependencies_pending = 'dependencies_pending'
    dependencies_ready = 'dependencies_ready'
    declared = 'declared'

    # transition 2: declared -> exists
    fetching = 'fetching'
    found = 'found'  # goes immediately to 'exists'
    doesnt_exist = 'doesnt_exist'
    creating = 'creating'
    creation_request_succeeded = 'creation_request_succeeded'
    creation_request_failed = 'creation_request_failed'
    exists = 'exists'  # confirmed with another fetch after creation

    # transition 3: exists -> healthy
    checking_health = 'checking_health'
    healthy = 'healthy'
    unhealthy = 'unhealthy'

    # transition 4: exists/healthy -> deleted
    deleting = 'deleting'
    deleted = 'deleted'

    # transition 5: healthy -> updated -> healthy
    updating = 'updating'
    updated = 'updated'

    error = 'error'
    warning = 'warning'
'''


class ResourceEventTypeEnum(BaseStrEnum):

    # 1. ensure_dependencies_ready
    dependencies_pending = 'dependencies_pending'
    dependencies_ready = 'dependencies_ready'

    # 2. ensure_exists
    resource_found = 'resource_found'
    resource_not_found = 'resource_not_found'
    creating = 'creating'
    creation_request_succeeded = 'creation_request_succeeded'
    creation_request_failed = 'creation_request_failed'

    # 3. ensure_healthy
    health_check_failed = 'health_check_failed'  # i.e. a retry exception
    health_checks_terminated = 'health_checks_terminated'
    health_checks_succeeded = 'health_checks_succeeded'

    # health_check_failed, health_checks_terminated

    # 4. ensure_updated
    updating = 'updating'
    updated = 'updated'

    # 5. ensure_deleted
    deleting = 'deleting'
    deleted = 'deleted'

    error = 'error'
    warning = 'warning'
    terminal_failure = 'terminal_failure'


class DesiredStateEnum(BaseStrEnum):

    healthy = 'healthy'
    deleted = 'deleted'
    updated = 'updated'
    untracked = 'untracked'


class ResourceStateEnum(BaseStrEnum):

    newborn_model = 'newborn_model'
    dependencies_pending = 'dependencies_pending'
    declared = 'declared'
    exists = 'exists'
    doesnt_exist = 'doesnt_exist'
    healthy = 'healthy'
    deleted = 'deleted'
    unknown = 'unknown'
    creation_terminated = 'creation_terminated'


class ExistenceEnum(BaseStrEnum):

    exists = 'exists'
    doesnt_exist = 'doesnt_exist'
    unknown = 'unknown'
    # also 'checking' ?


class HealthEnum(BaseStrEnum):

    healthy = 'healthy'
    unhealthy = 'unhealthy'
    unknown = 'unknown'

