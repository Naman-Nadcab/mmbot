import base64

from mmbot.security.hardening import AdvancedRbacEngine, RbacPolicy, SignedAuditChain, TotpMfaVerifier


def test_totp_verifier_generates_and_verifies_code():
    secret = base64.b32encode(b"supersecretkey12").decode().rstrip("=")
    verifier = TotpMfaVerifier(window=0)
    code = verifier.generate(secret, at_time=60)
    assert verifier.verify(secret, code, at_time=60) is True


def test_signed_audit_chain_detects_tampering():
    chain = SignedAuditChain("signing-key")
    first = chain.append({"event": "CONFIG_UPDATE"})
    second = chain.append({"event": "KILL_SWITCH"})
    assert chain.verify([first, second]) is True
    tampered = type(first)(first.sequence, {"event": "ALTERED"}, first.previous_signature, first.signature)
    assert chain.verify([tampered, second]) is False


def test_advanced_rbac_requires_mfa_when_policy_demands_it():
    engine = AdvancedRbacEngine([RbacPolicy("risk_manager", "risk", "write", requires_mfa=True)])
    assert engine.authorize({"risk_manager"}, "risk", "write", mfa_verified=False) is False
    assert engine.authorize({"risk_manager"}, "risk", "write", mfa_verified=True) is True
