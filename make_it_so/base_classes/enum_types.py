from enum import Enum

import structlog


logger = structlog.get_logger(__name__)


class BaseStrEnum(str, Enum):
    """
    Abstract class intended to be used for all state Enums
    inherits from str for easy comparisons
    """

    @classmethod
    def choices(cls):
        return [(c.value, c.name) for c in cls]

    @classmethod
    def has_value(cls, value):
        values = set(item.value for item in cls)
        if type(value) is not str:
            value = value.value
        return value in values


class BaseIntEnum(int, Enum):
    """
    Abstract class intended to be used for all state Enums
    inherits from str for easy comparisons
    """

    @classmethod
    def choices(cls):
        return [(c.value, c.name) for c in cls]

    @classmethod
    def has_value(cls, value):
        values = set(item.value for item in cls)
        if type(value) is not int:
            value = value.value
        return value in values


class BaseTupleEnum(tuple, Enum):
    """
    Abstract class intended to be used for all state Enums
    inherits from tuple for easy comparisons
    """

    @classmethod
    def choices(cls):
        return [(c.value, c.name) for c in cls]

    @classmethod
    def has_value(cls, value):
        values = set(item.value for item in cls)
        if type(value) is not tuple:
            value = value.value
        return value in values
