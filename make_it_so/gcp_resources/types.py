
REGIONS = [
    'us-east1', 'us-east1', 'us-east1', 'us-east4', 'us-east4', 'us-east4', 'us-central1', 'us-central1', 'us-central1', 'us-central1', 'us-west1', 'us-west1', 'us-west1', 'europe-west4', 'europe-west4', 'europe-west4', 'europe-west1', 'europe-west1', 'europe-west1', 'europe-west3', 'europe-west3', 'europe-west3', 'europe-west2', 'europe-west2', 'europe-west2', 'asia-east1', 'asia-east1', 'asia-east1', 'asia-southeast1', 'asia-southeast1', 'asia-southeast1', 'asia-northeast1', 'asia-northeast1', 'asia-northeast1', 'asia-south1', 'asia-south1', 'asia-south1', 'australia-southeast1', 'australia-southeast1', 'australia-southeast1', 'southamerica-east1', 'southamerica-east1', 'southamerica-east1', 'asia-east2', 'asia-east2', 'asia-east2', 'asia-northeast2', 'asia-northeast2', 'asia-northeast2', 'asia-northeast3', 'asia-northeast3', 'asia-northeast3', 'asia-south2', 'asia-south2', 'asia-south2', 'asia-southeast2', 'asia-southeast2', 'asia-southeast2', 'australia-southeast2', 'australia-southeast2', 'australia-southeast2', 'europe-central2', 'europe-central2', 'europe-central2', 'europe-north1', 'europe-north1', 'europe-north1', 'europe-west6', 'europe-west6', 'europe-west6', 'northamerica-northeast1', 'northamerica-northeast1', 'northamerica-northeast1', 'northamerica-northeast2', 'northamerica-northeast2', 'northamerica-northeast2', 'southamerica-west1', 'southamerica-west1', 'southamerica-west1', 'us-west2', 'us-west2', 'us-west2', 'us-west3', 'us-west3', 'us-west3', 'us-west4', 'us-west4', 'us-west4'
]
REGION_CHOICES = [(s, s) for s in REGIONS]


ZONES = [
    'us-east1-b', 'us-east1-c', 'us-east1-d', 'us-east4-c', 'us-east4-b', 'us-east4-a', 'us-central1-c', 'us-central1-a', 'us-central1-f',
    'us-central1-b', 'us-west1-b', 'us-west1-c', 'us-west1-a', 'europe-west4-a', 'europe-west4-b', 'europe-west4-c', 'europe-west1-b',
    'europe-west1-d', 'europe-west1-c', 'europe-west3-c', 'europe-west3-a', 'europe-west3-b', 'europe-west2-c', 'europe-west2-b', 'europe-west2-a',
    'asia-east1-b', 'asia-east1-a', 'asia-east1-c', 'asia-southeast1-b', 'asia-southeast1-a', 'asia-southeast1-c', 'asia-northeast1-b',
    'asia-northeast1-c', 'asia-northeast1-a', 'asia-south1-c', 'asia-south1-b', 'asia-south1-a', 'australia-southeast1-b', 'australia-southeast1-c',
    'australia-southeast1-a', 'southamerica-east1-b', 'southamerica-east1-c', 'southamerica-east1-a', 'asia-east2-a', 'asia-east2-b', 'asia-east2-c',
    'asia-northeast2-a', 'asia-northeast2-b', 'asia-northeast2-c', 'asia-northeast3-a', 'asia-northeast3-b', 'asia-northeast3-c', 'asia-south2-a',
    'asia-south2-b', 'asia-south2-c', 'asia-southeast2-a', 'asia-southeast2-b', 'asia-southeast2-c', 'australia-southeast2-a', 'australia-southeast2-b',
    'australia-southeast2-c', 'europe-central2-a', 'europe-central2-b', 'europe-central2-c', 'europe-north1-a', 'europe-north1-b', 'europe-north1-c',
    'europe-west6-a', 'europe-west6-b', 'europe-west6-c', 'northamerica-northeast1-a', 'northamerica-northeast1-b', 'northamerica-northeast1-c',
    'northamerica-northeast2-a', 'northamerica-northeast2-b', 'northamerica-northeast2-c', 'southamerica-west1-a', 'southamerica-west1-b',
    'southamerica-west1-c', 'us-west2-a', 'us-west2-b', 'us-west2-c', 'us-west3-a', 'us-west3-b', 'us-west3-c', 'us-west4-a', 'us-west4-b', 'us-west4-c'
]
ZONE_CHOICES = [(s, s) for s in ZONES]


MACHINE_TYPES = [  # incomplete
    'n1-standard-%s' % i for i in [1, 2, 4, 8, 16, 32, 64, 96]
]
MACHINE_TYPE_CHOICES = [(s, s) for s in MACHINE_TYPES]
