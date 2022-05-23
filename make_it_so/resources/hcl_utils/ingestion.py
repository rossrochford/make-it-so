
from builtins import breakpoint
from collections import defaultdict

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.exceptions import ObjectDoesNotExist
from celery.contrib import rdb

from pydantic import ValidationError as PydanticValidationError
import structlog

from base_classes.pydantic_models import stringify_pydantic_validation_error
from resources import get_resource_classes
from resources.hcl_utils.parsing import parse_hcl_file
from resources.models import ResourceModel, ResourceDependencyModel
from transitions.celery_utils.exceptions import TaskFailureException
from users.models import ProjectModel


logger = structlog.get_logger(__name__)


def fetch_project_from_provider_block(provider_block):
    # provider_type = provider_block.get('provider_type')
    return ProjectModel.objects.filter(id=provider_block['project_id']).first()


def get_resource_from_hcl(ResourceClass, hcl_entry, project):
    ModelClass = ResourceClass.MODEL_CLASS
    model_query = ModelClass.objects.filter(hcl_slug=hcl_entry.fullname)
    model_obj = model_query.first()

    group = ResourceClass.GROUP_CLASS(project=project)
    group.add_resource(
        ResourceClass(model_obj, group)
    )
    return group, model_obj


def create_resource_from_hcl(
    hcl_entry, resource_classes, other_resources=None
):
    if other_resources:
        hcl_entry.evaluate_expressions(other_resources)

    ResourceClass = resource_classes[hcl_entry.rtype]
    ModelClass = ResourceClass.RESOURCE_MODEL_CLASS
    ExtraModelClass = ResourceClass.EXTRA_FIELDS_MODEL_CLASS
    provider_id_field = ResourceClass.PROVIDER_ID_FIELD

    tup = _collect_model_fields(hcl_entry, ResourceClass)
    new_values, extra_data, new_m2m_values, model_obj = tup

    if provider_id_field in ('slug', 'id'):
        pass

    # If exclude is set, a temporary subclass of ExtraModelClass is generated
    # with the field set as optional. This is so generate_provider_id() can use
    # fields on ExtraModelClass via the model_obj.extra attrdict. Once the provider id
    # is set, we proceed to validate against the original pydantic model,
    # replace the attrdict obj and write the validated data to model_obj.extra_data.
    try:
        exl = None if provider_id_field in ('slug', 'id') else [provider_id_field]
        extra_obj = ExtraModelClass.validate_and_create(extra_data, exclude=exl)
        extra_attr_dict = extra_obj.create_attr_dict(throw_missing=True)
    except PydanticValidationError as e:
        raise TaskFailureException(
            'hcl_validation_failed',
            reason=stringify_pydantic_validation_error(e)
        )
    except ObjectDoesNotExist as e:
        raise TaskFailureException(
            'hcl_validation_failed', reason=str(e)
        )

    model_obj._extra_attrdict = extra_attr_dict  # so .extra prop works
    try:
        model_obj.full_clean()  # triggers validators on model fields
        ResourceClass.clean(model_obj)
    except (DjangoValidationError, ValueError) as e:
        raise TaskFailureException(
            'hcl_validation_failed', reason=f'hcl clean failed: {e}'
        )

    if provider_id_field not in ('slug', 'id'):  # is it possible we might want to use generate_provider_id() to generate a slug?
        provider_id = ResourceClass.generate_provider_id(model_obj)
        extra_data[provider_id_field] = provider_id
        try:
            extra_obj = ExtraModelClass(**extra_data)
        except PydanticValidationError as e:
            raise TaskFailureException(
                'hcl_validation_failed',
                reason=stringify_pydantic_validation_error(e)
            )
        model_obj._extra_attrdict = extra_obj.create_attr_dict()

    model_obj.extra_data = extra_obj.dict()  # data is now ready to be saved

    if new_m2m_values:
        logger.warning(
            'ManyToMany fields are not yet supported', cls=ModelClass
        )
        '''for field_name, m2m_pks in new_m2m_values.items():
            for pk in m2m_pks:
                getattr(model_obj, field_name).add(pk)'''

    model_obj.save()
    created = True

    for field_name in ExtraModelClass.get_resource_fk_field_names():
        if model_obj.extra_data.get(field_name):
            dep_id = model_obj.extra_data[field_name]
            ResourceDependencyModel.objects.get_or_create(
                resource_id=model_obj.id, depends_on_id=dep_id,
                field_name=field_name
            )

    if other_resources:
        other_resources[hcl_entry.fullname] = ResourceClass(model_obj, None)

    return created, model_obj


def _collect_model_fields(hcl_entry, ResourceClass):
    entry_fields = set([k for k in hcl_entry.rdict.keys()])
    model_fields = set()

    ModelClass = ResourceClass.RESOURCE_MODEL_CLASS
    ExtraModelClass = ResourceClass.EXTRA_FIELDS_MODEL_CLASS
    extra_fields = set(ExtraModelClass.get_field_names())

    for f in ModelClass._meta.fields:
        if f.__class__.__name__ == 'ForeignKey':
            model_fields.add(f'{f.name}_id')
            continue
        model_fields.add(f.name)

    new_values = {
        fn: hcl_entry.rdict[fn] for fn in
        model_fields.intersection(entry_fields)
    }
    new_values['hcl_slug'] = hcl_entry.fullname
    new_values['rtype'] = hcl_entry.rtype

    new_m2m_values = defaultdict(list)
    for f in ModelClass._meta.many_to_many:
        if f.name in entry_fields:
            for val in hcl_entry.rdict[f.name]:
                if type(val) is not int:
                    breakpoint()
                    continue
                new_m2m_values[f.name].append(val)

    try:
        model_obj = ResourceModel(**new_values)
    except DjangoValidationError as e:
        reason = f'model validation failed: {e}'  # todo: use e.errors?
        raise TaskFailureException(
            'hcl_validation_failed', reason=reason, info={'inner_exc': str(e)}
        )

    extra_model_values = {
        fn: hcl_entry.rdict[fn] for fn in
        extra_fields.intersection(entry_fields)
    }
    for key in ExtraModelClass.get_resource_fk_field_names():
        id_key = f'{key}_id'
        if id_key in hcl_entry.rdict:
            extra_model_values[id_key] = hcl_entry.rdict[id_key]  # keep id_key for Resource.clean()
            extra_model_values[key] = hcl_entry.rdict[id_key]

    return new_values, extra_model_values, new_m2m_values, model_obj


def _fetch_hcl_resource_models(hcl_entries, project):

    existing_objects, existing_by_name = [], {}

    slugs = [e.fullname for e in hcl_entries]
    query = ResourceModel.objects.select_related('project').filter(
        hcl_slug__in=slugs, project=project
    )
    for resource_model in query:
        existing_by_name[resource_model.hcl_slug] = resource_model
        existing_objects.append(resource_model)

    return existing_objects, existing_by_name


def parse_hcl_and_fetch_resource_models(
    filepath=None, file_content=None, project=None
):
    locals, hcl_entries, entries_by_name, provider_block = parse_hcl_file(
        filepath=filepath, file_content=file_content
    )
    if project is None:
        project = fetch_project_from_provider_block(provider_block)
        if project is None:
            raise TaskFailureException('project in hcl provider not found')

    _, existing_by_name = _fetch_hcl_resource_models(hcl_entries, project)

    hcl_entries_by_name = {e.fullname: e for e in hcl_entries}

    return hcl_entries_by_name, existing_by_name


def create_hcl_resource_models(
    filepath=None, file_content=None, project=None
):
    RESOURCE_CLASSES = get_resource_classes()

    locals, hcl_entries, entries_by_name, provider_block = parse_hcl_file(
        filepath=filepath, file_content=file_content
    )
    if project is None:
        project = fetch_project_from_provider_block(provider_block)
        if project is None:
            raise TaskFailureException('project in hcl provider not found')

    existing, existing_by_name = _fetch_hcl_resource_models(hcl_entries, project)

    new_objects = []
    for hcl_entry in hcl_entries:
        if hcl_entry.fullname not in existing_by_name:
            _, model_obj = create_resource_from_hcl(
                hcl_entry, RESOURCE_CLASSES, other_resources=existing_by_name
            )
            existing_by_name[model_obj.hcl_slug] = model_obj
            new_objects.append(model_obj)
        # else:
            # update_model(obj, hcl_entry)  # updates not yet supported
    return existing, new_objects
