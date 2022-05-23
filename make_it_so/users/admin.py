from django.contrib import admin

from users.models import (
    UserModel, AccountModel, ProjectModel
)


admin.site.register(UserModel)
admin.site.register(AccountModel)
admin.site.register(ProjectModel)
