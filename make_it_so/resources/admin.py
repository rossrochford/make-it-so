from django.contrib import admin

from resources.models import (
    ResourceEventModel, ResourceModel, ResourceDependencyModel
)


admin.site.register(ResourceModel)
admin.site.register(ResourceEventModel)
admin.site.register(ResourceDependencyModel)
