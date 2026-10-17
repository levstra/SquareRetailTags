import boto3
import pytest
from botocore.stub import Stubber

from esl.accounts.identity import CognitoIdentityProvider, EmailAlreadyRegistered, IdentityError

POOL = "us-west-2_TESTPOOL"


@pytest.fixture
def cognito():
    client = boto3.client(
        "cognito-idp", region_name="us-west-2",
        aws_access_key_id="test", aws_secret_access_key="test",
    )
    with Stubber(client) as stub:
        yield client, stub, CognitoIdentityProvider(region="us-west-2", user_pool_id=POOL, client=client)


def test_invite_creates_user_and_returns_sub(cognito):
    _client, stub, idp = cognito
    stub.add_response(
        "admin_create_user",
        {"User": {"Username": "u-1", "Attributes": [
            {"Name": "sub", "Value": "sub-abc"}, {"Name": "email", "Value": "new@shop.test"}]}},
        {"UserPoolId": POOL, "Username": "new@shop.test",
         "UserAttributes": [{"Name": "email", "Value": "new@shop.test"},
                            {"Name": "email_verified", "Value": "true"}],
         "DesiredDeliveryMediums": ["EMAIL"]},
    )
    assert idp.invite("new@shop.test") == "sub-abc"


def test_invite_existing_email(cognito):
    _client, stub, idp = cognito
    stub.add_client_error("admin_create_user", service_error_code="UsernameExistsException")
    with pytest.raises(EmailAlreadyRegistered):
        idp.invite("taken@shop.test")


def test_other_errors_become_identity_error(cognito):
    _client, stub, idp = cognito
    stub.add_client_error("admin_disable_user", service_error_code="InternalErrorException")
    with pytest.raises(IdentityError):
        idp.disable("a@shop.test")


def test_disable_enable_delete_use_email_as_username(cognito):
    _client, stub, idp = cognito
    for op in ("admin_disable_user", "admin_enable_user", "admin_delete_user"):
        stub.add_response(op, {}, {"UserPoolId": POOL, "Username": "a@shop.test"})
    idp.disable("a@shop.test")
    idp.enable("a@shop.test")
    idp.delete("a@shop.test")
    stub.assert_no_pending_responses()
