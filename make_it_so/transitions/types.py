from base_classes.enum_types import BaseStrEnum


class TransitionTypeEnum(BaseStrEnum):

    ensure_dependencies_ready = 'ensure_dependencies_ready'
    ensure_exists = 'ensure_exists'
    ensure_healthy = 'ensure_healthy'

    ensure_forward_dependencies_deleted = 'ensure_forward_dependencies_deleted'
    ensure_deleted = 'ensure_deleted'

    ensure_updated = 'ensure_updated'
    test = 'test'


class TransitionStatusEnum(BaseStrEnum):
    pending = 'pending'
    sent_to_broker = 'sent_to_broker'
    in_progress = 'in_progress'
    succeeded = 'succeeded'
    failed = 'failed'


