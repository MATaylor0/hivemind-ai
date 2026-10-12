from __future__ import annotations

import time

import pytest

jwt = pytest.importorskip("jwt")
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from hivemind.hub.auth import EntraAuthenticator, KeyAuthenticator, identities_from_deployed  # noqa: E402
from hivemind.hub.service import HubError  # noqa: E402
from hivemind.providers.base import DeployedAgent  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
AUDIENCE = "api://hivemind-hub"
PROJECT_IDENTITY = "pppp"  # shared by unpublished agents
PUBLISHED_IDENTITY = "cccc"  # chief-of-staff, published to M365

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(oid: str, **overrides) -> str:
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": AUDIENCE,
        "tid": TENANT,
        "oid": oid,
        "exp": int(time.time()) + 300,
        **overrides,
    }
    return jwt.encode(claims, KEY, algorithm="RS256")


def deployed(name: str, principal: str) -> DeployedAgent:
    return DeployedAgent(name=name, version="1", content_hash="h", spec_name=name, principal_id=principal)


@pytest.fixture
def auth():
    fleet = {
        "chief-of-staff": deployed("chief-of-staff", PUBLISHED_IDENTITY),
        "ap-clerk": deployed("ap-clerk", PROJECT_IDENTITY),
        "fpa-analyst": deployed("fpa-analyst", PROJECT_IDENTITY),
    }
    return EntraAuthenticator(
        TENANT, AUDIENCE, lambda: identities_from_deployed(fleet), signing_key=lambda _: KEY.public_key()
    )


def headers(tok: str, agent: str | None = None) -> dict[str, str]:
    out = {"Authorization": f"Bearer {tok}"}
    if agent:
        out["x-hivemind-agent"] = agent
    return out


def test_published_agent_is_identified_by_its_own_token(auth):
    assert auth.identify(headers(token(PUBLISHED_IDENTITY))) == "chief-of-staff"
    with pytest.raises(HubError, match="token belongs to 'chief-of-staff', not 'ap-clerk'"):
        auth.identify(headers(token(PUBLISHED_IDENTITY), "ap-clerk"))


def test_shared_project_identity_needs_a_matching_header(auth):
    assert auth.identify(headers(token(PROJECT_IDENTITY), "ap-clerk")) == "ap-clerk"
    with pytest.raises(HubError, match="requires a valid x-hivemind-agent"):
        auth.identify(headers(token(PROJECT_IDENTITY)))
    # a project-identity caller cannot claim to be the published agent
    with pytest.raises(HubError, match="requires a valid"):
        auth.identify(headers(token(PROJECT_IDENTITY), "chief-of-staff"))


@pytest.mark.parametrize(
    "bad",
    [
        {"aud": "api://something-else"},
        {"iss": "https://login.microsoftonline.com/other-tenant/v2.0"},
        {"exp": int(time.time()) - 10},
    ],
)
def test_invalid_tokens_rejected(auth, bad):
    with pytest.raises(HubError, match="invalid token"):
        auth.identify(headers(token(PROJECT_IDENTITY, **bad), "ap-clerk"))


def test_unknown_identity_and_missing_token(auth):
    with pytest.raises(HubError, match="does not belong to a HiveMind agent"):
        auth.identify(headers(token("someone-else")))
    with pytest.raises(HubError, match="missing bearer token"):
        auth.identify({"x-hivemind-agent": "ap-clerk"})


def test_key_authenticator():
    auth = KeyAuthenticator("secret")
    assert auth.identify({"X-HiveMind-Key": "secret", "x-hivemind-agent": "ap-clerk"}) == "ap-clerk"
    with pytest.raises(HubError, match="invalid hub key"):
        auth.identify({"x-hivemind-key": "wrong", "x-hivemind-agent": "ap-clerk"})
