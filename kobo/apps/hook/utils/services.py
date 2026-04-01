from ..models.hook import Hook
from ..tasks import service_definition_task


def call_services(asset_uid: str, submission_id: int) -> bool:
    """
    Delegates to Celery data submission to remote servers.
    Hooks are triggered both for new submissions and edits.
    """
    # Retrieve `Hook` ids, to send data to their respective endpoint.
    hooks_ids = (
        Hook.objects.filter(asset__uid=asset_uid, active=True)
        .values_list('id', flat=True)
        .distinct()
    )

    success = bool(hooks_ids)

    for hook_id in hooks_ids:
        service_definition_task.delay(hook_id, submission_id)
    return success
