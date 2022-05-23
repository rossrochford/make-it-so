from typing import Dict, List, Optional

import structlog
import pydantic

from base_classes.enum_types import BaseStrEnum
from base_classes.pydantic_models import (
    PydanticBaseModel, ResourceForeignKey, IPv4CidrRange
)
from gcp_resources.resources.base_resource import GcpResource, GcpExtraResourceFieldsBase
from resources.utils import ResourceApiListResponse


logger = structlog.get_logger(__name__)


class ProtocolEnum(BaseStrEnum):
    tcp = 'tcp'
    udp = 'udp'
    icmp = 'icmp'
    esp = 'esp'
    ah = 'ah'
    sctp = 'sctp'
    ipip = 'ipip'
    all = 'all'


class FirewallDirectionEnum(BaseStrEnum):
    INGRESS = 'INGRESS'
    EGRESS = 'EGRESS'


class FirewallRule(PydanticBaseModel):
    IPProtocol: ProtocolEnum
    ports: List[str]

    @pydantic.validator('ports')
    def validate_ports(cls, li):
        for port_str in li:
            for port in port_str.split('-'):
                if not port.isdigit():
                    raise ValueError('invalid port found')
        return li


class GcpFirewallResourceFields(GcpExtraResourceFieldsBase):

    network: ResourceForeignKey('gcp_resources.GcpVpcNetworkResource')

    priority: int
    direction: FirewallDirectionEnum

    source_ranges: Optional[list[IPv4CidrRange]] = None  # consider adding model for these
    destination_ranges: Optional[list[IPv4CidrRange]] = None

    target_tags: Optional[list[str]] = None
    source_tags: Optional[list[str]] = None

    allow_rules: Optional[list[FirewallRule]] = None
    deny_rules: Optional[list[FirewallRule]] = None

    enable_logging: bool = False


class GcpFirewallResource(GcpResource):

    EXTRA_FIELDS_MODEL_CLASS = GcpFirewallResourceFields

    @staticmethod
    def generate_provider_id(model_obj):
        project_id = model_obj.project.slug
        return f'https://www.googleapis.com/compute/v1/projects/{project_id}/global/firewalls/{model_obj.slug}'

    @classmethod
    def list_resources(cls, cli, project) -> Dict[str, ResourceApiListResponse]:
        return {
            resp.provider_id: resp for resp in cli.list_firewalls(project.slug)
        }

    def create_resource(self):
        obj = self.model_obj
        gcp_project_id = obj.project.slug
        assert obj.x.network.x.self_link is not None
        success, self_link, response = self.cli.create_firewall(
            gcp_project_id,
            obj.slug,
            obj.x.network.x.self_link,
            obj.extra.priority,
            obj.extra.source_ranges,
            obj.extra.destination_ranges,
            obj.extra.source_tags,
            obj.extra.target_tags,
            obj.extra.allow_rules,
            obj.extra.deny_rules,
            enable_logging=obj.extra.enable_logging
        )
        return success, self_link, response

    def delete_resource(self):
        obj = self.model_obj
        gcp_project_id = obj.project.slug
        response = self.cli.delete_firewall(gcp_project_id, obj.slug)
        return True, response
