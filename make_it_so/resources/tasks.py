
from celery import shared_task
import structlog

from resources.models import ResourceModel
from resources.hcl_utils.ingestion import (
    create_hcl_resource_models, parse_hcl_and_fetch_resource_models
)
from users.models import ProjectModel


logger = structlog.get_logger(__name__)


@shared_task(bind=True)
def hcl_ingest_models(self, project_pk, hcl_file_content):
    return _hcl_ingest_models(project_pk, hcl_file_content)


def _hcl_ingest_models(project_pk, hcl_file_content):
    project = ProjectModel.objects.get(pk=project_pk)
    existing_objects, new_objects = create_hcl_resource_models(
        file_content=hcl_file_content, project=project
    )
    return True


@shared_task(bind=True)
def hcl_express_desired_state(
    self, project_pk, hcl_file_content, desired_state
):
    return _hcl_express_desired_state(
        project_pk, hcl_file_content, desired_state
    )


def _hcl_express_desired_state(
    project_pk, hcl_file_content, desired_state
):
    project = ProjectModel.objects.get(pk=project_pk)

    hcl_entries_by_name, existing_by_name = parse_hcl_and_fetch_resource_models(
        file_content=hcl_file_content, project=project
    )

    in_file = set(hcl_entries_by_name.keys())
    in_db = set(existing_by_name.keys())

    if not in_file.issubset(in_db):
        missing = in_file - in_db
        logger.error(
            'hcl resources are not fully ingested', missing=missing
        )
        return False

    resource_ids = [obj.id for obj in existing_by_name.values()]

    state = 'unknown'
    if desired_state == 'healthy':
        # may need to be more nuanced if we optimize create_missing_transitions()
        state = 'declared'

    ResourceModel.objects.filter(id__in=resource_ids).update(
        desired_state=desired_state, state=state
    )

    return True

