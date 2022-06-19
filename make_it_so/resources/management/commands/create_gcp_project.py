import json
import os

from django.core.management.base import BaseCommand
import structlog

from gcp_resources.gcloud_client import GcloudClient, validate_project_id, GCP_ENABLED_SERVICES
from users.models import AccountModel, ProjectModel


logger = structlog.get_logger(__name__)


def _prompt_selection(selections):
    prompt_str = '\n'.join([
        f'{key}: {val}' for (key, val) in selections.items()
    ])
    while True:
        answer = input(prompt_str).strip()
        if answer.isdigit():
            answer = int(answer)  #  assuming dict keys are never digit strings
        if answer not in selections:
            continue
        return answer, selections[answer]


def _prompt_yes_no(msg):
    while True:
        answer = input(msg).lower().strip()
        if answer in ('y', 'n'):
            return answer


def _prompt_for_project_id(existing_projects):
    while True:
        project_id = input('enter a project_id: ').strip().lower()
        if project_id in existing_projects:
            print(f'project with id "{project_id}" already exists')
            continue
        if validate_project_id(project_id) is False:
            print(f'invalid project id "{project_id}"')
            continue
        return project_id


def _prompt_for_credentials_filepath():
    while True:
        credentials_filepath = input('enter an absolute path for your credentials json file (e.g. /home/me/my-gcp-creds.json) : ').strip()
        if credentials_filepath.endswith('.json') is False:
            print('path must be a .json file name')
            continue
        if credentials_filepath[0] != '/':
            print('must be an absolute path')
            continue
        return credentials_filepath


class Command(BaseCommand):

    def handle(self, *args, **kwargs):

        account_obj = AccountModel.objects.filter(slug='nobody-account').first()
        if account_obj is None:
            exit('expected AccountModel "nobody-account" to exist, '
                 'please run: python manage.py init_db')

        succ = GcloudClient.check_binary_exists()
        if succ is False:
            exit('You do not have the gcloud sdk installed, go to: https://cloud.google.com/sdk/docs/install')

        cli = GcloudClient()

        admin_email = cli.check_is_authenticated()
        if admin_email:
            answer = _prompt_yes_no(
                f'authenticated as {admin_email}, continue? (y/n)'
            )
            if answer == 'n':
                cli.logout()
                admin_email = None

        if admin_email is None:
            cli.authenticate()
            admin_email = cli.check_is_authenticated()
            if admin_email is None:
                exit('authentication failed')

        billing_accounts, _ = cli.list_billing_accounts()
        if not billing_accounts:
            exit(
                'no active billing accounts found, add one '
                'to proceed: https://console.cloud.google.com/billing'
            )
        billing_account = billing_accounts[0]  # choose first account
        print(f'selecting billing account: {billing_account}')

        projects_linked = cli.list_projects_on_billing_account(billing_account)
        if len(projects_linked) >= 5:
            exit(f'too many projects linked to billing account: {billing_account}, cannot proceed')

        existing_projects, _ = cli.list_projects()

        project_id = _prompt_for_project_id(existing_projects)
        credentials_filepath = _prompt_for_credentials_filepath()

        succ, reason, _ = cli.create_project(project_id)
        if succ is False:
            exit(f'project creation failed: {reason}')

        cli.set_default_project(project_id)

        print(f'linking billing account {billing_account} to {project_id}')
        cli.link_billing_account_to_project(billing_account, project_id)

        cli.enable_services(GCP_ENABLED_SERVICES, project_id)

        sa_email = cli.create_service_account(f'sa-{project_id}', project_id)
        cli.prepare_service_account_as_editor(sa_email, project_id, admin_email)

        succ, reason = cli.create_service_account_key(
            sa_email, credentials_filepath, project_id
        )
        if succ is False:
            exit(f'failed to create service account key: {reason}')

        ProjectModel.get_or_create_gcp_project_model(
            credentials_filepath, account_obj
        )