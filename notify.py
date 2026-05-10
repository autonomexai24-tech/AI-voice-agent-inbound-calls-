from backend.logging import get_logger

logger = get_logger("notify")


def notify_booking_confirmed(*args, **kwargs) -> bool:
    logger.info("notify.legacy_booking_confirmed_disabled")
    return False


def notify_booking_cancelled(*args, **kwargs) -> bool:
    logger.info("notify.legacy_booking_cancelled_disabled")
    return False


def notify_call_no_booking(*args, **kwargs) -> bool:
    logger.info("notify.legacy_no_booking_disabled")
    return False


def notify_agent_error(*args, **kwargs) -> bool:
    logger.info("notify.legacy_agent_error_disabled")
    return False
