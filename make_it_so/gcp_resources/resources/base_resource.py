from typing import Optional

import structlog

from base_classes.pydantic_models import PydanticBaseModel
from gcp_resources.api_client import GcpApiClient
from resources.base_resource import ResourceBase, ProviderBase


logger = structlog.get_logger(__name__)


# self_link is mandatory because we're using it as the identifier ('fetch_existing'
# needs this), therefore when declaring resources in advance, it must be possible to
# generate it before the underlying resource has been created. If this is not possible
# you must override the self_link field (setting blank/null=True) and implement
# get_provider_identifier() to use a different id (for example the slug)

# def get_provider_identifier(self):
"""
It's important that identifiers can be generated before the
underlying resource has been created. In GCP often the self_link
can be anticipated.

When not possible we should fall back to the slug by overriding this
method. Resource slugs are unique per resource type within a project
and fetch_existing()'s behavior mimics this.
"""


class GcpExtraResourceFieldsBase(PydanticBaseModel):
    self_link: str
    self_id: Optional[str] = None


class GcpProvider(ProviderBase):

    @classmethod
    def create_cli(cls, rtype, project):
        return GcpApiClient(project.credentials)


class GcpResource(ResourceBase):

    PROVIDER = GcpProvider
    PROVIDER_ID_FIELD = 'self_link'
    # MODEL_CLASS = GcpResourceModel

    def exists_hook(
        self, creation_response=None, list_response=None,
        provider_id=None
    ):
        obj = self.model_obj
        self_id, self_link = None, None
        resp = creation_response or list_response

        assert creation_response or list_response
        assert not (creation_response and list_response)

        if creation_response:
            self_id = resp['id']
            self_link = resp.get('targetLink') or resp['target_link']

        if list_response:
            self_id = resp['id']
            self_link = resp.get('selfLink') or resp['self_link']

        if provider_id and self_link and provider_id != self_link:
            logger.warning(
                'provider_id and self_link do not match',
                provider_id=provider_id, self_link=self_link
            )

        if provider_id:  # this takes precedence
            self_link = provider_id

        if self_id:
            obj.extra_data['self_id'] = self_id
        if self_link:
            obj.extra_data['self_link'] = self_link

        if self_id or self_link:
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
