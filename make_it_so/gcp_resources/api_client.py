import re
import sys
from typing import Dict, Union, List

from googleapiclient import discovery
from googleapiclient.errors import HttpError
import google.cloud.compute_v1 as compute_v1
from google.cloud.compute_v1 import Operation
from google.oauth2 import service_account
import structlog
# from oauth2client.client import GoogleCredentials

from resources.utils import ResourceApiListResponse


GcpStatus = Operation.Status

# SLUG_REGEX = r'(?:[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?)'  # from gcloud client output
# STATUS_NUMBERS = {e.value: e.name for e in GcpStatus}


# note: the main API (not the protobuf version we're using here) uses strings for status
# and the value for 'running' differ: ('PROVISIONING', 'STAGING', 'RUNNING')
INSTANCE_RUNNING_STATUSES_INT = (
    GcpStatus.PENDING, GcpStatus.RUNNING
)

logger = structlog.get_logger(__name__)


def coerce_gcp_credentials(
    credentials: Union[service_account.Credentials, Dict]
) -> service_account.Credentials:
    if isinstance(credentials, service_account.Credentials):
        return credentials
    if not isinstance(credentials, dict):
        raise ValueError(f"Unexpected type for gcp credentials: {type(credentials)}")
    return service_account.Credentials.from_service_account_info(credentials)


class GcpApiListResponse(ResourceApiListResponse, dict):

    @property
    def provider_id(self):
        return self.get('selfLink') or self.get('self_link')


class GcpApiClient:

    def __init__(self, credentials):
        self.credentials = coerce_gcp_credentials(credentials)
        self.compute_service = discovery.build(
            'compute', 'v1', credentials=self.credentials,
            cache_discovery=False
        )

    def create_vpc_network(
        self, gcp_project_id, name, routing_mode=None, mtu=1460,
        auto_create_subnetworks=True
    ):
        # https://cloud.google.com/compute/docs/reference/rest/v1/networks/insert
        network_body = {
            "name": name, "mtu": mtu,
            "autoCreateSubnetworks": auto_create_subnetworks,
            # note: VPC networks must have autoCreateSubnetworks=True and "IPv4Range" unset
        }
        if routing_mode:
            assert routing_mode in ('REGIONAL', 'GLOBAL')
            network_body['routingConfig'] = {'routingMode': routing_mode}

        assert auto_create_subnetworks  # for now, only this is supported

        request = self.compute_service.networks().insert(
            project=gcp_project_id, body=network_body
        )
        try:
            response = request.execute()
        except HttpError as e:
            if e.status_code == 409:  # already exists
                response = self.get_network(gcp_project_id, name)
                # note: response structure may differ between get vs create endpoints
                return True, response['selfLink'], response
            raise e

        success = response['status'] == 'RUNNING'
        self_link = response['targetLink']  # quirk of GCP

        return success, self_link, response

    def list_networks(self, gcp_project_id) -> List[GcpApiListResponse]:
        networks = []
        request = self.compute_service.networks().list(project=gcp_project_id)
        while request is not None:
            response = request.execute()
            for network_di in response['items']:
                networks.append(
                    GcpApiListResponse(network_di)
                )
            request = self.compute_service.networks().list_next(
                previous_request=request, previous_response=response
            )
        return networks

    def get_network(self, gcp_project_id, network_name):
        request = self.compute_service.networks().get(
            project=gcp_project_id, network=network_name
        )
        response = request.execute()

        return response

    def delete_network(self, gcp_project_id, network_name):
        request = self.compute_service.networks().delete(
            project=gcp_project_id, network=network_name
        )
        response = request.execute()
        return response

    def create_firewall(
        self, gcp_project_id, name, network_link, priority, source_ranges,
        destination_ranges, source_tags, target_tags, allowed, denied,
        enable_logging=False
    ):
        firewall_body = dict(
            name=name, network=network_link, priority=priority,
            sourceRanges=source_ranges, destinationRanges=destination_ranges,
            sourceTags=source_tags, targetTags=target_tags, allowed=allowed,
            denied=denied
        )
        if enable_logging:
            firewall_body['logConfig'] = {
                'enable': enable_logging, 'metadata': "INCLUDE_ALL_METADATA"
            }
        request = self.compute_service.firewalls().insert(
            project=gcp_project_id, body=firewall_body
        )
        try:
            response = request.execute()
        except HttpError as e:
            if e.status_code == 409:  # already exists
                breakpoint()
            raise e

        if response['status'] != 'RUNNING':
            logger.info(
                'unexpected firewall status', status=response['status']
            )

        success = response['status'] == 'RUNNING'
        self_link = response['targetLink']  # quirk of GCP

        return success, self_link, response

    def list_firewalls(self, gcp_project_id) -> List[GcpApiListResponse]:
        request = self.compute_service.firewalls().list(project=gcp_project_id)
        firewalls = []
        while request is not None:
            response = request.execute()
            for firewall_di in response['items']:
                firewalls.append(
                    GcpApiListResponse(firewall_di)
                )
            request = self.compute_service.firewalls().list_next(
                previous_request=request, previous_response=response
            )
        return firewalls

    def delete_firewall(self, project_id, name):
        request = self.compute_service.firewalls().delete(
            project=project_id, firewall=name
        )
        response = request.execute()
        ''' {'resp': {'kind': 'compute#operation', 'id': '6208095549856274122', 'name': 'operation-1651533861358-5de0fb1240c49-e2354de0-17022949', 'operationType': 'delete', 'targetLink': 'https://www.googleapis.com/compute/v1/projects/declarative-test-1/global/firewalls/allow-ssh', 'targetId': '1200717815104349454', 'status': 'RUNNING', 'user': 'declarative-sa@declarative-test-1.iam.gserviceaccount.com', 'progress': 0, 'insertTime': '2022-05-02T16:24:21.777-07:00', 'startTime': '2022-05-02T16:24:21.792-07:00', 'selfLink': 'https://www.googleapis.com/compute/v1/projects/declarative-test-1/global/operations/operation-1651533861358-5de0fb1240c49-e2354de0-17022949'}}
        '''
        return response

    def create_instance(
        self,
        project_id: str,
        zone: str,
        instance_name: str,
        machine_type: str = "n1-standard-1",
        source_image: str = "projects/debian-cloud/global/images/family/debian-10",
        network_name: str = "global/networks/default",
        wait: bool = False
    ):

        # based on: https://github.com/googleapis/python-compute/blob/17b95c3/samples/snippets/quickstart.py
        # unclear how it differs with: https://cloud.google.com/compute/docs/reference/rest/v1/instances/insert
        # but this gives us type validation for free, the protobuf request raises a ValueError in __setattr__

        request = self._create_instance_insertion_request(
            project_id, zone, instance_name,
            machine_type=machine_type, source_image=source_image,
            network_name=network_name
        )
        instance_client = compute_v1.InstancesClient(credentials=self.credentials)
        operation_resp = instance_client.insert_unary(request=request)

        if wait:
            succ = self._wait_for_instance(
                project_id, zone, operation_resp
            )
        else:
            # status values: PROVISIONING, STAGING, RUNNING, STOPPING, SUSPENDING, SUSPENDED, REPAIRING, and TERMINATED
            # see: https://cloud.google.com/compute/docs/instances/instance-life-cycle
            status = operation_resp.status
            succ = bool(operation_resp.error) is False and status in INSTANCE_RUNNING_STATUSES_INT

        return succ, operation_resp.target_link, operation_resp

    @staticmethod
    def _create_instance_insertion_request(
        project_id: str,
        zone: str,
        instance_name: str,
        machine_type: str = "n1-standard-1",
        source_image: str = "projects/debian-cloud/global/images/family/debian-10",
        network_name: str = "global/networks/default",
    ) -> compute_v1.Instance:
        """
            machine_type: machine type of the VM being created. This value uses the
                e.g. "zones/europe-west3-c/machineTypes/f1-micro"
            source_image: path to the operating system image to mount on your boot
                disk, e.g. "projects/debian-cloud/global/images/family/debian-10"
        """

        # Describe the size and source image of the boot disk to attach to the instance.
        # I've seen contradictory info on disks suggesting these need to be created upfront
        # and have naming-uniqueness constraints, but the code below is fine?
        disk = compute_v1.AttachedDisk()
        initialize_params = compute_v1.AttachedDiskInitializeParams()
        initialize_params.source_image = source_image
        initialize_params.disk_size_gb = 10
        disk.initialize_params = initialize_params
        disk.auto_delete = True
        disk.boot = True
        disk.type_ = "PERSISTENT"

        # Use the network interface provided in the network_name argument.
        network_interface = compute_v1.NetworkInterface()
        network_interface.name = network_name

        # Collect information into the Instance object.
        instance = compute_v1.Instance()
        instance.name = instance_name
        instance.network_interfaces = [network_interface]
        instance.disks = [disk]
        if re.match(r"^zones/[a-z\d\-]+/machineTypes/[a-z\d\-]+$", machine_type):
            instance.machine_type = machine_type
        else:
            instance.machine_type = f"zones/{zone}/machineTypes/{machine_type}"

        # Prepare the request to insert an instance.
        request = compute_v1.InsertInstanceRequest()
        request.zone = zone
        request.project = project_id
        request.instance_resource = instance
        return request

    def _wait_for_instance(self, gcp_project_id, zone, operation):

        operation_client = compute_v1.ZoneOperationsClient(
            credentials=self.credentials
        )
        while operation.status != compute_v1.Operation.Status.DONE:
            operation = operation_client.wait(
                operation=operation.name, zone=zone, project=gcp_project_id
            )
        if operation.error:
            print("Error during creation:", operation.error, file=sys.stderr)
            return False
        if operation.warnings:
            print("Warning during creation:", operation.warnings, file=sys.stderr)
        return True

    def delete_instance(self, project_id, zone, instance_name, wait=False):

        instance_client = compute_v1.InstancesClient(
            credentials=self.credentials
        )
        operation = instance_client.delete_unary(
            project=project_id, zone=zone, instance=instance_name
        )

        if wait:
            operation_client = compute_v1.ZoneOperationsClient()
            while operation.status != compute_v1.Operation.Status.DONE:
                operation = operation_client.wait(
                    operation=operation.name, zone=zone, project=project_id
                )
            if operation.error:
                print("Error during deletion:", operation.error, file=sys.stderr)
                return False
            if operation.warnings:
                print("Warning during deletion:", operation.warnings, file=sys.stderr)

        return operation

    def list_instances(self, project_id, with_statuses=None) -> List[GcpApiListResponse]:
        instances = []
        request = self.compute_service.instances().aggregatedList(project=project_id)
        statuses = set()
        while request is not None:
            response = request.execute()

            for name, collection in response['items'].items():
                if collection.get('warning', {}).get('code') == 'NO_RESULTS_ON_PAGE':
                    continue
                for instance_di in collection.get('instances'):
                    statuses.add(instance_di['status'])
                    if with_statuses and instance_di['status'] not in with_statuses:
                        continue
                    instances.append(
                        GcpApiListResponse(instance_di)
                    )
            request = self.compute_service.instances().aggregatedList_next(
                previous_request=request, previous_response=response
            )

        if len(statuses) > 1:  # curious which kind of statuses this returns
            print(f'instance statuses: {statuses}')

        return instances


'''
# 'https://www.googleapis.com/compute/v1/projects/declarative-test-1/zones/europe-west3-c/instances/test-instance'


'BOOL', 'BYTES', 'DOUBLE', 'ENUM', 'FIXED32', 'FIXED64', 'FLOAT', 'INT32', 'INT64', 'MESSAGE', 'SFIXED32', 'SFIXED64', 'SINT32', 'SINT64', 'STRING', 'UINT32', 'UINT64'

{
  "name": string,
  "description": string,
  "network": string,
  "priority": integer,
  "sourceRanges": [
    string
  ],
  "destinationRanges": [
    string
  ],
  "sourceTags": [
    string
  ],
  "targetTags": [
    string
  ],
  "sourceServiceAccounts": [
    string
  ],
  "targetServiceAccounts": [
    string
  ],
  "allowed": [
    {
      "IPProtocol": string,
      "ports": [
        string
      ]
    }
  ],
  "denied": [
    {
      "IPProtocol": string,
      "ports": [
        string
      ]
    }
  ],
  "direction": enum,
  "logConfig": {
    "enable": boolean,
    "metadata": enum
  },
  "disabled": boolean,
  "selfLink": string,
  "kind": string
}
'''


'''
from pprint import pprint

from googleapiclient import discovery
from oauth2client.client import GoogleCredentials

credentials = GoogleCredentials.get_application_default()

service = discovery.build('compute', 'v1', credentials=credentials)

# Project ID for this request.
project = 'my-project'  # TODO: Update placeholder value.

firewall_body = {
    # TODO: Add desired entries to the request body.
}

request = service.firewalls().insert(project=project, body=firewall_body)
response = request.execute()

# TODO: Change code below to process the `response` dict:
pprint(response)
'''


# create network response
'''
{'id': '1942498833656293307',
 'insertTime': '2022-02-27T16:37:40.698-08:00',
 'kind': 'compute#operation',
 'name': 'operation-1646008660251-5d909417dd469-d5af3b2f-537c9057',
 'operationType': 'insert',
 'progress': 0,
 'selfLink': 'https://www.googleapis.com/compute/v1/projects/declarative-test-1/global/operations/operation-1646008660251-5d909417dd469-d5af3b2f-537c9057',
 'startTime': '2022-02-27T16:37:40.708-08:00',
 'status': 'RUNNING',
 'targetId': '2436561729307649979',
 'targetLink': 'https://www.googleapis.com/compute/v1/projects/declarative-test-1/global/networks/declarative-test-1-network',
 'user': 'declarative-sa@declarative-test-1.iam.gserviceaccount.com'}
'''


# list networks response
'''
{'id': '2436561729307649979', 'creationTimestamp': '2022-02-27T16:37:40.696-08:00',
'name': 'declarative-test-1-network', 'description': '',
'selfLink': 'https://www.googleapis.com/compute/v1/projects/declarative-test-1/global/networks/declarative-test-1-network',
'autoCreateSubnetworks': True,
'subnetworks': ['https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/asia-northeast2/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/us-east1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/us-west4/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/us-west2/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/us-central1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/southamerica-west1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/europe-west3/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/asia-south2/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/asia-southeast2/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/europe-west2/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/northamerica-northeast1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/asia-southeast1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/asia-northeast1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/us-west3/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/us-east4/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/asia-east1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/asia-east2/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/europe-west6/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/australia-southeast1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/us-west1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/northamerica-northeast2/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/europe-central2/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/asia-south1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/southamerica-east1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/europe-north1/subnetworks/declarative-test-1-network', 
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/asia-northeast3/subnetworks/declarative-test-1-network', 
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/europe-west1/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/australia-southeast2/subnetworks/declarative-test-1-network',
'https://www.googleapis.com/compute/v1/projects/declarative-test-1/regions/europe-west4/subnetworks/declarative-test-1-network'
],
'routingConfig': {'routingMode': 'REGIONAL'}, 'mtu': 1460, 'kind': 'compute#network'}
'''


# get network response
'''
{
  "id": string,
  "creationTimestamp": string,
  "name": string,
  "description": string,
  "IPv4Range": string,
  "gatewayIPv4": string,
  "selfLink": string,
  "selfLinkWithId": string,
  "autoCreateSubnetworks": boolean,
  "subnetworks": [
    string
  ],
  "peerings": [
    {
      "name": string,
      "network": string,
      "state": enum,
      "stateDetails": string,
      "autoCreateRoutes": boolean,
      "exportCustomRoutes": boolean,
      "importCustomRoutes": boolean,
      "exchangeSubnetRoutes": boolean,
      "exportSubnetRoutesWithPublicIp": boolean,
      "importSubnetRoutesWithPublicIp": boolean,
      "peerMtu": integer
    }
  ],
  "routingConfig": {
    "routingMode": enum
  },
  "mtu": integer,
  "kind": string
}
'''


