notification_router = AbstractEPRouter(
    prefix="/v1/notification",
    tags=["Notifications"],
    manager_factory=get_notification_manager,
    network_model_cls=NotificationNetworkModel,
    resource_name="notification",
    example_overrides=notification_examples,
)

user_notification_router = AbstractEPRouter(
    prefix="/v1/user-notification",
    tags=["Notifications"],
    manager_factory=get_user_notification_manager,
    network_model_cls=UserNotificationNetworkModel,
    resource_name="user_notification",
)


# Notification custom routes
@notification_router.patch(
    "/{id}/read",
    summary="Mark notification as read",
    description="Marks a notification as read.",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def mark_notification_read(
    id: str = Path(..., description="Notification ID"),
    manager=Depends(get_notification_manager),
):
    """Mark a notification as read"""
    from datetime import datetime

    now = datetime.utcnow()
    user_notification = manager.mark_as_read(notification_id=id, read_at=now)

    return {"user_notification": user_notification}


@notification_router.patch(
    "/{id}/acknowledge",
    summary="Acknowledge notification",
    description="Marks a notification as acknowledged.",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def acknowledge_notification(
    id: str = Path(..., description="Notification ID"),
    manager=Depends(get_notification_manager),
):
    """Acknowledge a notification"""
    from datetime import datetime

    now = datetime.utcnow()
    user_notification = manager.acknowledge(notification_id=id, acknowledged_at=now)

    return {"user_notification": user_notification}
