from esl.accounts.identity import EmailAlreadyRegistered


class FakeIdentityProvider:
    """In-memory stand-in for Cognito admin calls."""

    def __init__(self):
        self.users: dict[str, dict] = {}

    def invite(self, email: str) -> str:
        if email in self.users:
            raise EmailAlreadyRegistered(email)
        sub = f"sub-{email}"
        self.users[email] = {"sub": sub, "enabled": True}
        return sub

    def disable(self, email: str) -> None:
        self.users[email]["enabled"] = False

    def enable(self, email: str) -> None:
        self.users[email]["enabled"] = True

    def delete(self, email: str) -> None:
        self.users.pop(email, None)
