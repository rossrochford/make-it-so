import json
import uuid

from django.contrib.auth.models import AbstractUser
from django.core import validators
from django.db import models
from django.utils.translation import gettext_lazy as _

from base_classes.models import BaseModel
from resources.types import ProviderTypesEnum
from resources.models import ResourceModel


class UserModel(AbstractUser):

    username = models.CharField(
        _("username"),
        max_length=150,
        unique=True,
        validators=[validators.validate_slug]
    )
    account = models.ForeignKey(
        # allow null, however every user should have an account
        'AccountModel', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='members'
    )
    # active_project = models.ForeignKey(
    #     'ProjectModel', blank=True, null=True, on_delete=models.SET_NULL
    # )
    is_admin = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)

    class Meta(AbstractUser.Meta):
        indexes = [models.Index(fields=["username"])]


class ProfileModel(BaseModel):
    user = models.OneToOneField(
        UserModel, on_delete=models.CASCADE, related_name="profile"
    )
    ssh_public_key = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        return f"Profile: {self.user}"


class AccountModel(BaseModel):
    slug = models.SlugField(max_length=127, unique=True)
    name = models.CharField(max_length=255)

    def user_has_access(self, user=None, user_id=None):
        assert user or user_id
        if user_id is None:
            user_id = user.id
        member_ids = [user.id for user in self.members.all()]
        return user_id in member_ids


class ProjectModel(BaseModel):

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    slug = models.CharField(
        max_length=47, #validators=[validate_slug]
    )

    account = models.ForeignKey(
        AccountModel, on_delete=models.CASCADE
    )
    provider_type = models.CharField(
        max_length=64, choices=ProviderTypesEnum.choices()
    )
    provider_specific_data = models.JSONField(default=dict, blank=False)
    credentials = models.JSONField(default=dict, blank=False)  # todo: encrypt

    # future work: EXTRA_FIELDS_MODEL_CLASS

    @property
    def project(self):  # will this prevent a project column?
        return self

    class Meta(ResourceModel.Meta):
        abstract = False
        unique_together = None

    def __str__(self):
        return f'{self.__class__.__name__} {self.slug}'

    @classmethod
    def get_or_create_gcp_project_model(cls, credentials_filepath, account_obj):

        with open(credentials_filepath) as f:
            credentials_json = json.loads(f.read())

        project_id = credentials_json['project_id']

        gcp_project, created = ProjectModel.objects.get_or_create(
            slug=project_id, account=account_obj, provider_type='google',
            defaults={'credentials': credentials_json}
        )
        print(f'\nProjectModel.id: {gcp_project.pk}\n')

        if not created:
            gcp_project.credentials = credentials_json
            gcp_project.save()

        return gcp_project

