from typing import Optional

import structlog

from base_classes.pydantic_models import PydanticBaseModel
from gcp_resources.api_client import GcpApiClient
from resources.base_resource import ResourceBase, ProviderBase, ResourceIdentifier


logger = structlog.get_logger(__name__)


class GcpExtraResourceFieldsBase(PydanticBaseModel):
    self_link: str
    self_id: Optional[str] = None


class GcpProvider(ProviderBase):

    @classmethod
    def create_cli(cls, rtype, project):
        return GcpApiClient(project.credentials)


class GcpResourceIdentifier(ResourceIdentifier):

    MODEL_FIELD = 'self_link'

    @staticmethod
    def get_id_from_list_response(list_resp):
        return list_resp.get('selfLink') or list_resp.get('self_link')

    @staticmethod
    def get_id_from_creation_response(creation_resp):
        return creation_resp.get('selfLink') or creation_resp.get('self_link')


class GcpResource(ResourceBase):

    PROVIDER = GcpProvider

    def exists_hook(
        self, creation_response=None, list_response=None
    ):
        obj = self.model_obj
        self_id, self_link = None, None
        resp = creation_response or list_response
        updated = False

        assert creation_response or list_response
        assert not (creation_response and list_response)

        if creation_response:
            self_id = resp['id']
            self_link = resp.get('targetLink') or resp['target_link']

        if list_response:
            self_id = resp['id']
            self_link = resp.get('selfLink') or resp['self_link']

        if self_id and obj.extra_data.get('self_id') != self_id:
            obj.extra_data['self_id'] = self_id
            updated = True
        if self_link and obj.extra_data.get('self_link') != self_link:
            obj.extra_data['self_link'] = self_link
            updated = True

        if updated:
            obj.save()

        obj.update_fields(
            creation_response=creation_response,
            list_response=list_response
        )

    def health_check__ensure_self_id_and_link_set(self):
        if self.model_obj.extra.self_id is None:
            return False, False
        if self.model_obj.extra.self_link is None:
            return False, False
        return True, None
