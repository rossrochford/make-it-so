
provider "google" {
  # insert your Project id here, this is MIS's internal id, not GCP's project_id
  project_id = "YOUR_PROJECT_ID"
  resources_app = "gcp_resources"
}

resource "GcpVpcNetworkResource" "test-network" {
  slug = "test-network"
  auto_create_subnetworks = true
}

resource "GcpFirewallResource" "allow-ssh" {
  slug = "allow-ssh"
  network_id = GcpVpcNetworkResource.test-network.id
  priority = 1000
  source_ranges = ["0.0.0.0/0"]
  direction = "INGRESS"
  target_tags = ["allow-ssh"]

  allow_rules = [
    {
      IPProtocol = "tcp"
      ports = ["22"]
    }
  ]
}

resource "GcpInstanceResource" "test-instance" {
  slug = "test-instance"
  network_id = GcpVpcNetworkResource.test-network.id
  zone = "europe-west3-c"
  source_image = "projects/debian-cloud/global/images/family/debian-10"
  machine_type = "n1-standard-1"
}
