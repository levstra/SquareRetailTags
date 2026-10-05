from square.utils.webhooks_helper import verify_signature

from esl.config import settings


class WebhookNotConfigured(Exception):
    pass


def is_valid_signature(body: str, signature: str) -> bool:
    if not (settings.square_webhook_signature_key and settings.square_webhook_url):
        raise WebhookNotConfigured()
    return verify_signature(
        request_body=body,
        signature_header=signature,
        signature_key=settings.square_webhook_signature_key,
        notification_url=settings.square_webhook_url,
    )
