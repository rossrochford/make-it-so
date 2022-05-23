from typing import Optional
from ipaddress import IPv4Address

from attrdict import AttrDict
from celery.contrib import rdb
from django.core.exceptions import ObjectDoesNotExist
import pydantic
from pydantic import ValidationError
import structlog

from resources.models import ResourceModel


logger = structlog.get_logger(__name__)


class PydanticBaseModel(pydantic.BaseModel):

    @classmethod
    def get_field_names(cls, alias=False):
        return list(cls.schema(alias).get("properties").keys())

    @classmethod
    def get_resource_fk_field_names(cls, alias=False):
        field_names = []
        for key, di in cls.schema(alias).get('properties').items():
            if 'type' in di and di['type'] == 'ResourceForeignKey':
                field_names.append(key)
        return field_names

    @classmethod
    def validate_and_create(cls, data_dict, exclude=None):
        TempModelClass = cls
        if exclude:
            kwargs = {'__base__': cls}
            for field_name in exclude:
                kwargs[field_name] = (Optional[str], None)
            TempModelClass = pydantic.create_model('TempModel', **kwargs)

        # throws ValidationError if data is invalid
        return TempModelClass(**data_dict)

    def create_attr_dict(self, throw_missing=False):
        """
            creates an AttrDict populated with any ResourceModels
            referenced in foreign key fields
        """
        fk_fields = self.get_resource_fk_field_names()
        extra_data = self.dict()
        resource_ids = [
            val for (key, val) in extra_data.items() if key in fk_fields
        ]
        if not resource_ids:
            return AttrDict(extra_data or {})

        resources_by_id = {
            obj.id: obj for obj in
            ResourceModel.objects.filter(id__in=resource_ids)
        }
        for key in fk_fields:
            val = extra_data.get(key)
            if val is None:
                continue
            if val in resources_by_id:
                extra_data[key] = resources_by_id[val]
            else:
                logger.warning(
                    'resource in extra_data not found', key=key, id=val
                )
                if throw_missing:
                    raise ObjectDoesNotExist(
                        f'resource for "{key}" missing, id: {val}'
                    )

        return AttrDict(extra_data or {})


class _ResourceForeignKey(str):

    RTYPE = None

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def __modify_schema__(cls, field_schema):
        field_schema.update(type='ResourceForeignKey')

    @classmethod
    def validate(cls, v):
        if not isinstance(v, str):
            raise TypeError('string required')

        resource = ResourceModel.objects.filter(id=v).first()
        if resource is None:
            raise ValueError(f'Resource with id="{v}" not found')
        if resource.rtype != cls.RTYPE:
            raise ValueError(
                f'unexpected Resource rtype, expected: {cls.RTYPE}, '
                f'found: {resource.rtype}'
            )
        return cls(v)

    def __repr__(self):
        return f'ResourceForeignKey({super().__repr__()})'


def ResourceForeignKey(rtype):
    """ create a subclass with rtype set"""
    class ResourceFkClass(_ResourceForeignKey):
        RTYPE = rtype
    return ResourceFkClass


class IPv4CidrRange(str):

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not isinstance(v, str):
            raise TypeError('string required')

        ip_addr, rng = v.split('/')

        if rng.isdigit() is False:
            raise ValidationError(f'invalid cidr range: {v}')
        if int(rng) < 0 or int(rng) > 255:
            raise ValidationError(f'invalid cidr range: {v}')
        try:
            IPv4Address(ip_addr)
        except ValueError:
            raise ValidationError(f'invalid cidr range: {v}')

        return cls(v)


class TestModel(pydantic.BaseModel):
    network: ResourceForeignKey('gcp_resources.GcpVpcNetworkResource')


def stringify_pydantic_validation_error(exc):

    error_strings = []

    for error_dict in exc.errors():
        error_type = error_dict['type']
        if error_type == 'value_error.missing':
            field_name = error_dict['loc'][0]
            error_strings.append(
                f'missing field: {field_name}'
            )
            continue
        if error_type == 'value_error':
            field_name = error_dict['loc'][0]
            msg = error_dict['msg']
            error_strings.append(
                f'value error on {field_name}: {msg}'
            )
            continue
        logger.warning(f'unexpected pydantic error type: {error_type}')
        st = ''
        for key, val in error_dict.items():
            key = key.upper()
            st = st + f'__{key}:{val}'
        rdb.set_trace()
        print()

    return '\n'.join(error_strings)
