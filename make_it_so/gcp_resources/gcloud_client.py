import json
import os.path
from os.path import exists as path_exists
import re
import time
import uuid

from invoke import Context, UnexpectedExit


GCP_ENABLED_SERVICES = [
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "oslogin.googleapis.com",
    "cloudbilling.googleapis.com",
    "sourcerepo.googleapis.com",
    "cloudkms.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudasset.googleapis.com",
    # "secretmanager.googleapis.com",
    # "containerregistry.googleapis.com",
]

SA_ROLES = [
    "roles/serviceusage.serviceUsageAdmin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/compute.instanceAdmin",
    "roles/compute.networkAdmin",
    "roles/compute.securityAdmin",  # so it can create firewall rules
    "roles/compute.publicIpAdmin",
    "roles/compute.storageAdmin",
    # or for all compute* permissions grant: roles/compute.admin
]


def validate_project_id(project_id):
    project_id = project_id.strip().lower()

    if not (6 <= len(project_id) <= 64):
        return False

    regex = r'[a-z][a-z0-9\-]+[a-z0-9]'
    match = re.fullmatch(regex, project_id, flags=re.I)
    if match is None:
        return False

    return True


class GcloudClient:

    def __init__(self, ctx=None):
        self.ctx = ctx or Context()
        self.authenticated_cached = False

    @staticmethod
    def check_binary_exists():
        try:
            res = Context().run("gcloud --version", hide='both')
        except UnexpectedExit:
            return False
        if res.exited != 0:
            return False
        return True

    def list_accounts(self):
        res = self.ctx.run('gcloud auth list --format=json', hide='stdout')
        return [
            (di['account'].lower(), di['status'] == 'ACTIVE')
            for di in json.loads(res.stdout)
        ]

    def check_is_authenticated(self):
        for email, is_active in self.list_accounts():
            if is_active:
                return email
        return None

    def authenticate(self):
        res = self.ctx.run('gcloud auth login --brief --format=json')
        self.authenticated_cached = True
        # keys: ['client_id', 'client_secret', 'default_scopes', 'expired', 'expiry', 'id_token', 'quota_project_id',
        # 'rapt_token', 'refresh_handler', 'refresh_token', 'requires_scopes', 'scopes', 'token', 'token_uri', 'valid']
        return json.loads(res.stdout)

    def ensure_authenticated(self):
        admin_email = None
        while admin_email is None:
            admin_email = self.check_is_authenticated()
            if admin_email is None:
                self.authenticate()
        return admin_email

    def ensure_authenticated_as(self, target_email):
        while True:
            current_email = self.check_is_authenticated()
            if current_email == target_email:
                break
            self.authenticate()

    def logout(self):
        self.authenticated_cached = False
        try:
            self.ctx.run('gcloud auth revoke', hide='stdout')
        except:
            pass

    def list_projects(self):
        res = self.ctx.run('gcloud projects list --format=json', hide='stdout')
        results = json.loads(res.stdout)
        project_ids = [di['projectId'] for di in results]
        return project_ids, results

    def create_project(self, project_id):

        if validate_project_id(project_id) is False:
            return False, 'validation_failed', None

        try:
            res = self.ctx.run(f'gcloud projects create {project_id} --format=json')
        except UnexpectedExit as e:
            reason = None
            if 'project ID you specified is already in use by another project' in e.result.stderr:
                reason = 'project_id_in_use'
            return False, reason, e.result.stderr
        return True, None, json.loads(res.stdout)

    def set_default_project(self, project_id):
        self.ctx.run(f'gcloud config set project {project_id}')

    def list_regions(self):
        res = self.ctx.run('gcloud compute regions list --format=json', hide='stdout')
        regions = json.loads(res.stdout)
        return [di['name'] for di in regions]

    def set_default_region_for_project(self, project_id, region):
        self.ctx.run(
            f'gcloud compute project-info add-metadata '
            f'--metadata google-compute-default-region={region} --project={project_id}'
        )

    def list_instances(self, project_id):
        res = self.ctx.run(
            f'gcloud compute instances list --format=json --project={project_id}', hide='stdout'
        )
        return json.loads(res.stdout)

    def list_billing_accounts(self):
        res = self.ctx.run('gcloud beta billing accounts list --format=json', hide='stdout')

        account_ids, accounts_by_id = [], {}
        for account_dict in json.loads(res.stdout):
            if account_dict['open'] is False:
                continue
            id = account_dict['name'].split('/')[1]
            account_ids.append(id)
            accounts_by_id[id] = account_dict

        return account_ids, accounts_by_id

    def list_projects_on_billing_account(self, billing_account_id):
        res = self.ctx.run(
            f'gcloud beta billing projects list --billing-account={billing_account_id} --format=json',
            hide='stdout'
        )
        return [di['projectId'] for di in json.loads(res.stdout) if di['billingEnabled']]

    def link_billing_account_to_project(self, billing_account_id, project_id):
        res = self.ctx.run(
            f'gcloud beta billing projects link {project_id} '
            f'--billing-account={billing_account_id} --format=json',
        )
        return json.loads(res.stdout)

    def unlink_project_from_billing_account(self, project_id):
        self.ctx.run(
            f'gcloud beta billing projects unlink {project_id}', hide='stdout'
        )

    def describe_billing_account_on_project(self, project_id):
        res = self.ctx.run(
            f'gcloud alpha billing accounts projects describe {project_id} --format=json --verbosity=error',
            hide='stdout'
        )
        return json.loads(res.stdout)

    def remove_projects_from_billing_account(self, billing_account_id):
        project_ids = self.list_projects_on_billing_account(billing_account_id)
        for project_id in project_ids:
            self.unlink_project_from_billing_account(project_id)

    def _enable_services_chunk(self, services, project_id):
        success = True
        try:
            services_str = ' '.join(services)
            res = self.ctx.run(f'gcloud services enable {services_str} --project={project_id}')
        except UnexpectedExit:
            success = False
        else:
            if res.exited != 0:
                success = False
        return success

    def enable_services(self, services, project_id):
        if len(services) > 2:
            print(f'enabling {len(services)} GCP API services, this may take a few minutes')

        def _chunker(seq, size):
            return (seq[pos:pos + size] for pos in range(0, len(seq), size))

        for chunk in _chunker(services, 4):
            succ = self._enable_services_chunk(chunk, project_id)
            time.sleep(8)  # try not to hit writes/min limit

            if succ is False:  # sometimes fails, retry once
                succ = self._enable_services_chunk(chunk, project_id)

            if succ is False:
                return False
        return True

    def create_service_account(self, sa_name, project_id):
        res = self.ctx.run(
            f'gcloud iam service-accounts create {sa_name} --display-name={sa_name} '
            f'--project={project_id} --format=json'
        )
        di = json.loads(res.stdout)
        return di['email']

    def add_roles_to_service_account(self, sa_email, project_id, roles):
        for i, role in enumerate(roles):
            hide = None if i == len(roles) - 1 else 'both'
            self.ctx.run(
                f'gcloud projects add-iam-policy-binding {project_id} '
                f'--member="serviceAccount:{sa_email}" --role="{role}"',
                hide=hide
            )

    def create_service_account_key(self, sa_email, sa_key_filepath, project_id):

        sa_key_filepath = os.path.expanduser(sa_key_filepath)
        directory = os.path.dirname(sa_key_filepath)

        self.ctx.run(f'mkdir -p {directory}')
        self.ctx.run(f'rm -f {sa_key_filepath}')

        self.ctx.run(
            f'gcloud iam service-accounts keys create {sa_key_filepath} '
            f'--iam-account={sa_email} --project={project_id}'
        )
        return self.validate_credentials_file(sa_key_filepath, sa_email, project_id)

    def prepare_service_account_as_editor(self, sa_email, project_id, gcp_admin_email):

        # https://cloud.google.com/compute/docs/access/iam#the_serviceaccountuser_role
        roles = [
            'roles/editor', 'roles/iam.serviceAccountUser',
            'roles/compute.instanceAdmin.v1'
        ]
        for role in roles:
            self.ctx.run(
                f'gcloud projects add-iam-policy-binding {project_id} '
                f'--member="serviceAccount:{sa_email}" --role="{role}" ',
            )

    def prepare_service_account(self, sa_email, sa_key_filepath, project_id, gcp_admin_email):

        # create key file
        self.ctx.run(f'rm -f {sa_key_filepath}')
        self.ctx.run(f'gcloud iam service-accounts keys create {sa_key_filepath} --iam-account={sa_email}')

        # allow admin_email to use SA, and allow SA to act as itself
        self.ctx.run(
            f'gcloud iam service-accounts add-iam-policy-binding {sa_email} '
            f'--member="user:{gcp_admin_email}" --role="roles/iam.serviceAccountUser" ',
            hide='stdout'
        )
        self.ctx.run(
            f'gcloud iam service-accounts add-iam-policy-binding {sa_email} '
            f'--member="serviceAccount:{sa_email}" --role="roles/iam.serviceAccountUser" ',
            hide='stdout'
        )

        # grant roles to service account
        for i, role in enumerate(SA_ROLES):
            hide = None if i == 0 or i == len(SA_ROLES) - 1 else 'both'
            self.ctx.run(
                f'gcloud projects add-iam-policy-binding {project_id} '
                f'--member="serviceAccount:{sa_email}" --role="{role}"',
                hide=hide
            )

    @staticmethod
    def validate_credentials_file(sa_key_filepath, sa_email, project_id):
        if not path_exists(sa_key_filepath):
            return False, f'credentials file not found at: {sa_key_filepath}'

        expected = {
            'type': 'service_account',
            'project_id': project_id,
            'client_email': sa_email
        }
        with open(sa_key_filepath) as f:
            creds_data = json.loads(f.read())
            for key, val in expected.items():
                if val != creds_data[key]:
                    return False, f'unexpected values {key}={creds_data[key]}, expected: {val}'
        return True, None
