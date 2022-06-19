from typing import Dict, List

import structlog

from base_classes.enum_types import BaseStrEnum
from gcp_resources.resources.base_resource import GcpResource, GcpExtraResourceFieldsBase, GcpResourceIdentifier
from gcp_resources.resources.subnets import GcpSubnetResource
from resources.models import ResourceModel
from resources.utils import ResourceApiListResponse


logger = structlog.get_logger(__name__)


class RoutingModeEnum(BaseStrEnum):
    REGIONAL = 'REGIONAL'
    GLOBAL = 'GLOBAL'


class GcpNetworkResourceFields(GcpExtraResourceFieldsBase):
    routing_mode: RoutingModeEnum = RoutingModeEnum.GLOBAL
    mtu: int = 1460
    auto_create_subnetworks: bool = True


class GcpVpcNetworkIdentifier(GcpResourceIdentifier):

    @staticmethod
    def generate(resource_model):
        project_id = resource_model.project.slug
        return f'https://www.googleapis.com/compute/v1/projects/{project_id}/global/networks/{resource_model.slug}'


class GcpVpcNetworkResource(GcpResource):

    EXTRA_FIELDS_MODEL_CLASS = GcpNetworkResourceFields
    IDENTIFIER = GcpVpcNetworkIdentifier
    HAS_DEPENDENCIES = False
    RETRY_PARAMS = {
        'ensure_healthy': {
            'retry_backoff': 2,
            'max_retries': 15,
            'total_timeout': 4200
        }
    }

    @classmethod
    def list_resources(cls, cli, project) -> List:
        return cli.list_networks(project.slug)

    def create_resource(self):
        obj = self.model_obj
        gcp_project_id = obj.project.slug
        tup = self.cli.create_vpc_network(
            gcp_project_id, obj.slug,
            routing_mode=obj.extra.routing_mode, mtu=obj.extra.mtu,
            auto_create_subnetworks=obj.extra.auto_create_subnetworks
        )
        success, self_link, response = tup
        return success, response

    def delete_resource(self):
        network_obj = self.model_obj
        project_id = network_obj.project.slug
        response = self.cli.delete_network(project_id, network_obj.slug)
        return True, response

    def health_check__ensure_age_over_90s(self):
        age = self.model_obj.resource_age
        if age is None:  # can be None when resource is 'found'
            return True, None  # skip check by returning True
        if age <= 90:
            return False, False
        return True, None

    def health_check__ensure_subnetworks_created(self):
        network_obj = self.model_obj
        if network_obj.extra.auto_create_subnetworks is False:
            # here the health-check is irrelevant
            return True, None

        response = self.fetch()  # consider using getter_response on model
        if response and len(response.get('subnetworks', [])) > 20:
            return True, None

        return False, False

    def healthy_hook(self):

        network_obj = self.model_obj
        response = self.fetch()

        if response and response.get('subnetworks'):
            for link in response['subnetworks']:
                subnet_obj, created = self._create_subnet_model(network_obj, link)
                subnet_obj.log_event(
                    'resource_found_and_healthy', t=self.transition
                )
        else:
            logger.warning('no subnetwork links found', response=response)

    @staticmethod
    def _create_subnet_model(network_obj, subnet_link):
        region = GcpSubnetResource.get_region_from_self_link(subnet_link)
        slug = f'{network_obj.slug}-subnet_{region}'
        subnet_obj, created = ResourceModel.objects.get_or_create(
            slug=slug, project=network_obj.project,
            rtype='gcp_resources.GcpSubnetResource',
            defaults={
                'extra_data': {
                    'network': network_obj, 'self_link': subnet_link,
                    'region': region
                }
            }
        )
        return subnet_obj, created
