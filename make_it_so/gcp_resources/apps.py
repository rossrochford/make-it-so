from django.apps import AppConfig


class GcpResourcesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gcp_resources'

    def get_resource_classes(self):
        from gcp_resources.resources.firewalls import GcpFirewallResource
        from gcp_resources.resources.vpc_networks import GcpVpcNetworkResource
        from gcp_resources.resources.subnets import GcpSubnetResource
        from gcp_resources.resources.instances import GcpInstanceResource

        return [
            GcpFirewallResource, GcpVpcNetworkResource,
            GcpSubnetResource, GcpInstanceResource
        ]
