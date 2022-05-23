import re

import structlog

from base_classes.pydantic_models import ResourceForeignKey
from gcp_resources.resources.base_resource import GcpResource, GcpExtraResourceFieldsBase


logger = structlog.get_logger(__name__)



class GcpSubnetResourceFields(GcpExtraResourceFieldsBase):

    network: ResourceForeignKey('gcp_resources.GcpVpcNetworkResource')
    region: str  # validation isn't trivial, bc availability changes


class GcpSubnetResource(GcpResource):

    EXTRA_FIELDS_MODEL_CLASS = GcpSubnetResourceFields

    @staticmethod
    def generate_provider_id(model_obj):
        project_id = model_obj.project.slug
        network = model_obj.extra.network
        region = model_obj.extra.region
        return f'https://www.googleapis.com/compute/v1/projects/{project_id}/regions/{region}/subnetworks/{network.slug}'

    # note: HCL support isn't implemented yet so these are omitted:
    #def list_resources(cls, cli, project)
    #def create_resource(self):
    #def delete_resource(self):

    @staticmethod
    def get_region_from_self_link(self_link):
        reg = r'.*/regions/(?P<region>.+?)/subnetworks'
        match = re.search(reg, self_link, flags=re.I)
        assert match
        return match.groupdict()['region']


# use this to list subnets project-wide: # https://cloud.google.com/compute/docs/reference/rest/v1/subnetworks/aggregatedList
# note: we'll be using gcp's subnetworks/aggregatedList endpoint, which lists all subnets within a project (rather than a network)
# however if we did want to scope the query to the network, it implies that we need a way of overriding the base query used for grouping
