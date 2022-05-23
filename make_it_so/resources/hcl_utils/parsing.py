import os.path
from collections import defaultdict
import graphlib
import re

from celery.contrib import rdb
import hcl2

from transitions.celery_utils.exceptions import TaskFailureException


MANDATORY_PROVIDER_FIELDS = {
    'google': [
        ('project_id', str)
    ],
    'hetzner': [
        ('project_id', str)
    ]
}


def _parse_provider(hcl_dict):

    if len(hcl_dict.get('provider', [])) != 1:
        raise Exception('error: a single provider block must be included')

    provider_type, provider_block = [tup for tup in hcl_dict['provider'][0].items()][0]
    if provider_type not in ('google', 'hetzner'):
        raise Exception(
            'error: provider must be: "google" or "hetzner"'
        )
    for field_name, field_type in MANDATORY_PROVIDER_FIELDS[provider_type]:
        if field_name not in provider_block:
            raise Exception(f'error: provider missing field: {field_name}')
        if type(provider_block[field_name]) != field_type:
            raise Exception(
                f'error: provider field "{field_name}" has unexpected type: {field_type}'
            )

    provider_block['provider_type'] = provider_type
    return provider_block


def traverse(obj, func):
    obj_type = type(obj)

    if obj_type is list:
        for i, val in enumerate(obj):
            new_val, do_replace = traverse(val, func)
            if do_replace:
                obj[i] = new_val
        return None, False

    if obj_type is dict:
        for key, val in obj.items():
            new_val, do_replace = traverse(val, func)
            if do_replace:
                obj[key] = new_val
        return None, False

    return func(obj)


class HclEntry:

    def __init__(self, rtype, rname, rdict, provider_block, locals_di):

        self.rtype = rtype
        self.rname = rname
        self.rdict = rdict
        self.locals = locals_di
        self.provider_block = provider_block
        self.app_name = provider_block.get('resources_app')

        self._enrich_resource()
        self._evaluate_local_and_file_expressions()

    @property
    def fullname(self):
        return f'{self.rtype}.{self.rname}'

    def __str__(self):
        return f'HclEntry: {self.fullname}'

    def _parse_expr(self, val):
        components = val.strip('${}').split('.')
        expected_num = 3 if self.app_name else 4
        if len(components) != expected_num:
            raise Exception(
                f'unexpected number of components in '
                f'expression "{val}", expected {expected_num}'
            )
        rtype = components.pop(0)
        if self.app_name:
            rtype = f'{self.app_name}.{rtype}'

        name, attr_name = components
        return rtype, name, attr_name

    def get_dependencies(self):
        resource_names = []

        def append_if_expr(val):
            if type(val) is str and val.startswith('${'):
                rtype, name, attr_name = self._parse_expr(val)
                resource_names.append(f'{rtype}.{name}')
            return val, False

        traverse(self.rdict, append_if_expr)
        return resource_names

    def _enrich_resource(self):
        provider = self.provider_block
        self.rdict['project_id'] = provider['project_id']

    def _evaluate_local_and_file_expressions(self):

        def fetch_local_or_file_value(val):
            if type(val) is not str:
                return None, False
            if val.startswith('${local.'):
                local_key = val.split('.')[1].strip('}')
                new_val = self.locals[local_key]
                return new_val, True
            elif val.startswith('${file('):
                reg = r'.*"(?P<filepath>.+?)".*'
                match = re.search(reg, val)
                if match is None:
                    raise Exception('invalid file expression')
                rdb.set_trace()
                filepath = os.path.expanduser(match.groupdict()['filepath'])
                if not os.path.exists(filepath):
                    raise Exception(f'file not found: {filepath}')
                file_contents = open(filepath).read().strip()
                return file_contents, True

            return None, False

        traverse(self.rdict, fetch_local_or_file_value)

    def evaluate_expressions(self, other_entries):

        def retrieve_expr_value(val):
            if type(val) is str and val.startswith('${'):
                rtype, name, attr_name = self._parse_expr(val)
                fullname = f'{rtype}.{name}'
                new_val = other_entries[fullname].get_output(attr_name)
                return new_val, True
            return None, False

        traverse(self.rdict, retrieve_expr_value)


def parse_hcl_file(filepath=None, file_content=None):

    assert filepath or file_content

    none_tuple = (None, None, None, None)

    try:
        if filepath:
            with open(filepath) as file:
                hcl_dict = hcl2.load(file)
        else:
            hcl_dict = hcl2.loads(file_content)
    except:
        return none_tuple

    locals_di = {}
    for entry in hcl_dict.get('locals', []):
        locals_di.update(entry)

    provider_block = _parse_provider(hcl_dict)
    app_name = provider_block.get('resources_app')  # optional
    entries = []

    for entry_group in hcl_dict['resource']:
        rtype = [k for k in entry_group.keys()][0]

        for rname, rdict in entry_group[rtype].items():
            full_rtype = rtype
            if app_name and not rtype.startswith(app_name):
                full_rtype = f'{app_name}.{rtype}'
            if '.' not in full_rtype:
                raise TaskFailureException(
                    'hcl_validation_failed',
                    reason=f'app_name missing from rtype: "{full_rtype}"'
                )
            entry = HclEntry(
                full_rtype, rname, rdict, provider_block, locals_di
            )
            entries.append(entry)
            # inline_entries = _create_inline_entries(...)

    resources_by_type, resources_by_name = defaultdict(list), {}

    dependency_graph = {}
    for entry in entries:
        resources_by_type[entry.rtype].append(entry)
        resources_by_name[entry.fullname] = entry
        dependency_graph[entry.fullname] = entry.get_dependencies()

    try:
        ts = graphlib.TopologicalSorter(dependency_graph)
        creation_order = list(ts.static_order())
    except graphlib.CycleError:
        print('error: cycle found')
        return none_tuple

    ordered_resources = []
    for name, entry in resources_by_name.items():
        if name not in creation_order:  # add those without antecedents first
            ordered_resources.append(entry)

    while creation_order:
        name = creation_order.pop(0)
        if name.startswith(app_name) is False:
            breakpoint()
        ordered_resources.append(resources_by_name[name])

    return locals, ordered_resources, resources_by_name, provider_block


'''
def _create_inline_entries(rtype, rname, rdict, provider_block, locals_di):

    entries = []
    RESOURCE_CLASSES = get_resource_classes()
    ResourceClass = RESOURCE_CLASSES[rtype]

    if not ResourceClass.HCL_INLINES:
        return []
    for hcl_field_name, data_type, inline_rtype, func in ResourceClass.HCL_INLINES:
        if not rdict.get(hcl_field_name):
            continue
        if not isinstance(rdict[hcl_field_name], data_type):
            raise Exception(
                f'{hcl_field_name} is not the correct type, expected: {data_type}'
            )
        assert data_type is list  # for now only list is supported

        parent_fullname = f'{rtype}.{rname}'
        for i, block_di in enumerate(rdict[hcl_field_name]):
            inline_rname = f'{rname}-{hcl_field_name}-{i}'
            inline_entry = func(
                parent_fullname, block_di, inline_rname,
                provider_block, locals_di
            )
            entries.append(inline_entry)

    return entries
'''
