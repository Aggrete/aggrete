"""JWTVerifier against a locally generated key: accept good, reject bad."""
import asyncio, time
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from aggrete.auth import JWTVerifier, StaticTokens, identity_for

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.public_key().public_bytes(serialization.Encoding.PEM,
                                    serialization.PublicFormat.SubjectPublicKeyInfo).decode()
ISS, AUD = "https://idp.example.com/", "https://aggrete.example.com/mcp"


def mint(**over):
    claims = {"iss": ISS, "aud": AUD, "sub": "u-123", "email": "alice@corp.example",
              "exp": int(time.time()) + 300, "scope": "mcp"}
    claims.update(over)
    return jwt.encode(claims, KEY, algorithm="RS256")


def verifier(**kw):
    return JWTVerifier(ISS, AUD, public_key=PEM, required_scopes=["mcp"], **kw)


def test_valid_jwt_yields_identity_from_email():
    tok = asyncio.run(verifier().verify_token(mint()))
    assert tok and tok.subject == "u-123" and identity_for(tok) == "alice@corp.example"


def test_identity_claim_override():
    tok = asyncio.run(verifier().verify_token(mint()))
    assert identity_for(tok, "sub") == "u-123"


def test_wrong_issuer_audience_expiry_or_scope_rejected():
    v = verifier()
    for bad in [mint(iss="https://evil.example/"), mint(aud="other"),
                mint(exp=int(time.time()) - 120), mint(scope="read")]:
        assert asyncio.run(v.verify_token(bad)) is None


def test_tampered_signature_rejected():
    tok = mint()
    head, body, sig = tok.split(".")
    assert asyncio.run(verifier().verify_token(f"{head}.{body}.{sig[:-3]}AAA")) is None


def test_static_tokens_dev_mode():
    v = StaticTokens({"t1": {"subject": "dev@example.com"}})
    assert identity_for(asyncio.run(v.verify_token("t1"))) == "dev@example.com"
    assert asyncio.run(v.verify_token("nope")) is None
