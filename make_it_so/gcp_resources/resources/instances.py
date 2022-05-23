from typing import Dict, Literal

from django.core.exceptions import ValidationError
import structlog

from base_classes.pydantic_models import ResourceForeignKey
from gcp_resources.api_client import GcpApiClient
from gcp_resources.resources.base_resource import GcpResource, GcpExtraResourceFieldsBase
from gcp_resources.types import ZONES, MACHINE_TYPES
from resources.utils import ResourceApiListResponse
from users.models import ProjectModel


logger = structlog.get_logger(__name__)


ZONES_TUPLE = tuple(ZONES)
MACHINE_TYPES_TUPLE = tuple(MACHINE_TYPES)


class GcpInstanceResourceFields(GcpExtraResourceFieldsBase):

    network: ResourceForeignKey('gcp_resources.GcpVpcNetworkResource')

    zone: Literal[ZONES_TUPLE]
    source_image: str
    machine_type: Literal[MACHINE_TYPES_TUPLE]


class GcpInstanceResource(GcpResource):

    EXTRA_FIELDS_MODEL_CLASS = GcpInstanceResourceFields

    @staticmethod
    def generate_provider_id(model_obj):
        project_id = model_obj.project.slug
        zone = model_obj.extra.zone
        return f'https://www.googleapis.com/compute/v1/projects/{project_id}/zones/{zone}/instances/{model_obj.slug}'

    @classmethod
    def list_resources(cls, cli, project) -> Dict[str, ResourceApiListResponse]:
        # note: this risks attempts to create an instance that is merely being restarted
        responses = cli.list_instances(
            project.slug, with_statuses=('PROVISIONING', 'STAGING', 'RUNNING')
        )
        return {resp.provider_id: resp for resp in responses}

    def create_resource(self):

        instance = self.model_obj
        success, self_link, resp = self.cli.create_instance(
            project_id=instance.project.slug,
            instance_name=instance.slug,
            zone=instance.extra.zone,
            machine_type=instance.x.machine_type,  # notice the 'x'
            source_image=instance.x.source_image,
            network_name=instance.x.network.x.self_link
        )
        response_dict = type(resp).to_dict(resp)
        provider_id = self_link

        return success, provider_id, response_dict

    def delete_resource(self):
        obj = self.model_obj
        project_id = obj.project.slug
        response = self.cli.delete_instance(
            project_id, obj.x.zone, obj.slug, wait=False
        )
        return True, response

    @classmethod
    def clean(cls, model_obj):
        # note: gcp_project_id here is the pk, the naming is confusing
        project = ProjectModel.objects.get(id=model_obj.project_id)
        network = model_obj.extra.network  #ResourceModel.objects.get(id=extra_data['network_id'])
        try:
            GcpApiClient._create_instance_insertion_request(
                project.slug,
                zone=model_obj.x.zone,
                instance_name=model_obj.slug,
                machine_type=model_obj.x.machine_type,
                source_image=model_obj.x.source_image,
                network_name=network.x.self_link
            )
        except ValueError as e:
            raise ValidationError(f'request validation failed: {e}')
