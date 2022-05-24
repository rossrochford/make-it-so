# Make It So :hammer_and_wrench:

A generic declarative provisioning engine implemented in Python, Django and Celery.

## Overview

**TL;DR**: Make It So is a Terraform clone written in Python.

Make It So is designed to reduce the amount of sequential, imperative code required to create *things*, in general. 
It maps declarative structures (Resources) to concrete imperative steps (tasks in Celery). It frees you from having to worry (much) about problems such as:

* handling failures and retries
* waiting for dependencies
* deciding when to give up
* tearing down and cleaning up reliably


## Architecture Overview

Make It So uses Django's ORM to store its state and Celery to execute tasks. It consists of the following primary elements: Resources, Dependencies, Transitions, States, Events, Providers and Projects.

A **Resource** is a *thing* defined declaratively. Similar to Terraform we use **HCL** files to declare Resources. Resources have a *state*, *desired_state* and a list of *dependencies*. 
When a user expresses a *desired_state* on a resource, the system looks for discrepancies and takes any necessary actions bring it in line with the desired state.

**Dependencies** are other resources to be waited upon, for example a VM instance will depend on a VPC network.

A **Transition** represents a major change to a Resource's state. These are created and queued up, often by other Transitions, and finally executed as Celery tasks. Transitions are responsible for ensuring a specific outcome is achieved, these outcomes bring a Resource closer to a desired_state.
For example an 'ensure_exists' Transition is responsible for ensuring a Resource exists on the Provider/Project, if it doesn't exist the Transition will attempt to create it.

**Events** are units of activity logged on Resources and Transitions, they not only provide an audit trail for debugging but may also propagate side effects and state changes.

A **Provider** is the point of access to a 3rd-party system that creates the underlying Resources, for example a cloud provider. Typically, a Provider doesn't do very much, it simply knows how to instantiate the API Client(s) that Resource implementations need. 

A **Project** stores credentials for interacting with a Provider, this typically corresponds to some kind of user or account identity on the Provider.  Projects also act as a kind of container/group for related Resources and tend to 'scope' the query behavior of API clients.


## Getting started: Google Cloud Resources

To illustrate how it works, Make It So includes a basic *gcp_resources* app for provisioning Resources in Google Cloud. 


### Installation prerequisites

* Poetry: https://python-poetry.org/docs/#installation
* Redis: https://redis.io/docs/getting-started/#install-redis


```bash
# clone repo
$ git clone git@github.com:rossrochford/make-it-so.git
$ cd make-it-so/

# setup a virtualenv with Poetry
$ poetry env use python3.9
$ poetry shell 
$ poetry install

# initialize the database
$ cd make_it_so/
$ python manage.py migrate
$ python manage.py init_db

# create a GCP project, note: you will need an active billing account on GCP.
# On completion it will output a unique Project.id, make note of this.
$ python manage.py create_gcp_project

# start Redis, celery workers, and celery-beat (in separate terminals)
$ redis-server  # this command may differ on your system
$ celery -A make_it_so worker --pool=gevent --concurrency=10
$ celery -A make_it_so beat

# optional: run Jaeger to capture tracing data
$ docker run -d --name jaeger \
  -e COLLECTOR_ZIPKIN_HOST_PORT=:9411 \
  -p 5775:5775/udp \
  -p 6831:6831/udp \
  -p 6832:6832/udp \
  -p 5778:5778 \
  -p 16686:16686 \
  -p 14250:14250 \
  -p 14268:14268 \
  -p 14269:14269 \
  -p 9411:9411 \
  jaegertracing/all-in-one:1.34
```

Here is an HCL file defining some GCP Resources: a VPC Network, a Firewall and an Instance. 

```hcl
// gcp_cluster_simple.tf

provider "google" {
  # note: this is primary key of the ProjectModel, not GCP's project_id
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

```

--------
To "apply" your Resources run:

```bash
# This imports the Resources with their desired_state set to 'healthy'. 
# The workers will notice and get to work immediately.
$ python manage.py hcl_apply gcp_cluster_simple.tf healthy
```

To view the Resources run the webserver:
```bash
$ python manage.py runserver
# visit: http://localhost:8000/admin and login with username and password: `nobody`
```


To destroy the Resources run:
```bash
# This will set the Resources' desired_state to 'deleted'. The underlying resources 
# will be deleted from the Provider but the Resource *models* will remain.
$ python manage.py hcl_apply gcp_cluster_simple.tf deleted
```

## Implementing a custom Resource

Let's look at the **GcpInstanceResource** implementation in: `gcp_resources/resources/instances.py`

This consists of 5 elements:

------------------------

1) A Pydantic model with custom fields:

```python
class GcpInstanceResource(GcpResource):

    EXTRA_FIELDS_MODEL_CLASS = GcpInstanceResourceFields

    
class GcpInstanceResourceFields(GcpExtraResourceFieldsBase):

    network: ResourceForeignKey('gcp_resources.GcpVpcNetworkResource')
    zone: Literal[ZONES_TUPLE]
    source_image: str
    machine_type: Literal[MACHINE_TYPES_TUPLE]

    
# gcp_resources/resources/base_resource.py
class GcpExtraResourceFieldsBase(PydanticBaseModel):
    self_link: str
    self_id: Optional[str] = None

```

Here we have defined some additional fields: *self_link, self_id, network, zone, source_image, machine_type*. All of these are strings except for *network*. 'Network' will be serialized as a string, an id of another ResourceModel, but it will be interpreted by the system as a **dependency**. 
An instance will not be scheduled for creation until its network is ready and healthy.

------------------------

2) A method for generating a provider_id, note: this is optional

```python

class GcpInstanceResource(GcpResource):

    @staticmethod
    def generate_provider_id(model_obj):
        project_id = model_obj.project.slug
        zone = model_obj.x.zone
        return f'https://www.googleapis.com/compute/v1/projects/{project_id}/zones/{zone}/instances/{model_obj.slug}'

    
# gcp_resources/resources/base_resource.py    
class GcpResource(ResourceBase):

    PROVIDER = GcpProvider
    PROVIDER_ID_FIELD = 'self_link'  
    # PROVIDER_ID_FIELD specifies where to on the model store the provider id, it must be set when implementing generate_provider_id()
```

When the system queries a Provider to check whether a Resource exists, it expects the API client to confirm or deny this by using a known *identifier*, unique within the Project.

The default of `ResourceModel.slug` is often sufficient, provided your Provider API client scopes its queries to the Project correctly. Here we're using GCP self_links here because they are easy to derive less ambiguous than *slug*.


#### A note on identifiers: 

A quirk of Make It So is that Resource identifiers must be knowable *before* the underlying Resource has been created. *ResourceModel.slug* is used by default because slugs are unique per Resource-type within a Project (enforced by the database). 

Your API clients **must** also key Resources by the same identifier and must scope its queries to a Project. Take care when sharing credentials across multiple projects as this may result in name conflicts.

------------------------

3) The `list_resources()` class-method:

This returns a collection of Resources from the Provider. This dictionary must be keyed by the appropriate provider_id and its results must be scoped to the current project.


```python

class GcpInstanceResource(GcpResource):

    @classmethod
    def list_resources(cls, cli, project) -> Dict[str, ResourceApiListResponse]:
        responses = cli.list_instances(
            project.slug, with_statuses=('PROVISIONING', 'STAGING', 'RUNNING')
        )
        return {resp.provider_id: resp for resp in responses}
```

For context here is how Provider creates the API client and where responses get wrapped in a `ResourceApiListResponse` class.

```python

# gcp_resources/resources/base_resource.py
class GcpProvider(ProviderBase):

    @classmethod
    def create_cli(cls, rtype, project):
        return GcpApiClient(project.credentials)


# gcp_resources/api_client.py
class GcpApiClient:
    
    def __init__(self, credentials):
        self.credentials = coerce_gcp_credentials(credentials)
    
    def list_instances(self, project_id, with_statuses=None) -> List[GcpApiListResponse]:
        # ...
        instances = [GcpApiListResponse(di) for di in instances]  # wrap response
        return instances

    
class GcpApiListResponse(ResourceApiListResponse, dict):

    @property
    def provider_id(self):
        return self.get('selfLink') or self.get('self_link')

```

------------------------

4) The `create_resource()` method:

```python

class GcpInstanceResource(GcpResource):

    def create_resource(self):

        instance = self.model_obj  # the ResourceModel

        success, self_link, resp = self.cli.create_instance(
            project_id=instance.project.slug,
            instance_name=instance.slug,
            zone=instance.x.zone,
            machine_type=instance.x.machine_type,  # notice the 'x'
            source_image=instance.x.source_image,
            network_name=instance.x.network.x.self_link
        )
        response_dict = type(resp).to_dict(resp)
        provider_id = self_link

        return success, provider_id, response_dict   # a 3-item tuple is expected

```

Here we fetch the ResourceModel and pass the relevant fields to the API client. 

Notice the 'x' attribute, this returns an AttrDict populated with data from the Pydantic model along with any related (ForeignKey) ResourceModels.

`create_resource()` will be executed within an `ensure_exists` Transition. This is scheduled only when a Resource's dependencies (here: `instance.x.network`) are ready and healthy.

------------------------

5) The `delete_resource()` method:

```python

class GcpInstanceResource(GcpResource):

    def delete_resource(self):
        obj = self.model_obj
        project_id = obj.project.slug
        response = self.cli.delete_instance(
            project_id, obj.x.zone, obj.slug, wait=False
        )
        return True, response

```

Dependencies also come into play here but in the opposite direction. If the network also has `desired_state="deleted"`", it will not be scheduled for deletion until instances on that network (that MIS knows about) have been deleted.

## Future work

Make It So is in early stages and **not** ready for production. Some outstanding work includes:

- Tenacity levels: how do we decide whether and when to give up? The system needs a 'never give up' mode where **desired_state** reigns supreme.
- The event-dispatch model is difficult to follow and reason about, it needs some deeper thought and documentation.
- Dashboard and visualizations: Make It So has basic tracing with opentelemetry and Jaeger, but it needs its own visualizations for inspecting the timeline of Transitions, Resources and Events.
- The way custom provider_ids are implemented is fragmented and error-prone. It needs a dedicated data structure.

## Internals: for the inquisitive

Make It So is not fully documented so looking at the code the best way to understand the system, here are some useful starting points:

* `resources/base_resource.py:ResourceBase` - The base class for all Resource implementations.  
* `resources/models.py:ResourceModel` - All Resource state/data is stored in this Django model/table. Any additional fields are defined on the Resource *implementation* class using a Pydantic model, this data is then serialized as json to *ResourceModel.extra_data*
* `gcp_resources/base_resource.py:GcpProvider` - Defines how the GCP API client is instantiated.
* `gcp_resources/base_resource.py:GcpResource`
* `gcp_resources/resources/vpc_networks.py:GcpVpcNetworkResource`
* `gcp_resources/apps.py:GcpResourcesConfig` - When you create a new django app for Resources, you must register its Resource classes in apps.py. The app name (e.g. 'gcp_resources') must also be referenced in your HCL file's provider block.

To understand how the scheduling and state-machine works, see:
* `transitions/tasks/ensure_exists.py` - A Celery Transition task, this executes to ensure a Resource 'exists'
* `transitions/celery_utils/task_class.py` - A custom Celery task class for Transitions. This adds custom retry/timeout logic to Celery, and propagates exceptions into actionable events.
* `transitions/tasks/daemon_tasks.py` - Daemon tasks, these run periodically, checking for any Transitions that need to be created or scheduled.
* `resources/models.py:ResourceModel.log_event()` - Events logged on Resources may cause state changes, future work will need to rethink this as it is difficult to reason about.
* `resources/models.py:ResourceDependencyModel` - How Resource dependencies are stored.
* `base_classes/pydantic_models.py:PydanticBaseModel` - Base Pydantic model class for representing 'extra' Resource fields, this includes magic for simulating foreign keys.
* `resources/hcl_utils/ingestion.py` - How HCL ingestion works (excuse the mess!)


