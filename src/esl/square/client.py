from square import Square
from square.environment import SquareEnvironment

from esl.config import settings


def environment() -> SquareEnvironment:
    # Explicit on purpose: the SDK defaults to PRODUCTION.
    if settings.square_environment.upper() == "SANDBOX":
        return SquareEnvironment.SANDBOX
    return SquareEnvironment.PRODUCTION


def client_for_token(access_token: str) -> Square:
    return Square(environment=environment(), token=access_token, version=settings.square_api_version)
