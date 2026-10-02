import base64
import json

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bamboozle.tessera import create_key, seal, unseal
from bamboozle.workspace import Workspace


def test_binary_packet_roundtrip_and_fresh_nonces():
    key = AESGCM.generate_key(bit_length=256)
    data = b"\x00\xffprivate\r\npayload"
    first, second = seal(data, key, "agent-a"), seal(data, key, "agent-a")
    assert first != second
    assert unseal(first, key, "agent-a") == data
    assert data not in first


@pytest.mark.parametrize("field", ["ciphertext", "nonce", "recipient"])
def test_packet_tampering_is_authenticated(field):
    key = AESGCM.generate_key(bit_length=256)
    packet = json.loads(seal(b"payload", key, "agent-a"))
    recipient = "agent-a"
    if field == "recipient":
        packet[field] = recipient = "agent-b"
    else:
        content = bytearray(base64.b64decode(packet[field]))
        content[0] ^= 1
        packet[field] = base64.b64encode(content).decode("ascii")
    with pytest.raises(ValueError, match="authentication failed"):
        unseal(json.dumps(packet).encode(), key, recipient)


def test_wrong_key_and_recipient_refused():
    key = AESGCM.generate_key(bit_length=256)
    packet = seal(b"payload", key, "agent-a")
    with pytest.raises(ValueError, match="different recipient"):
        unseal(packet, key, "agent-b")
    with pytest.raises(ValueError, match="authentication failed"):
        unseal(packet, AESGCM.generate_key(bit_length=256), "agent-a")


@pytest.mark.parametrize("recipient", ["", "../agent", "agent name", "代理", "a" * 129])
def test_invalid_recipient_refused(recipient):
    with pytest.raises(ValueError, match="Recipient"):
        seal(b"payload", b"x" * 32, recipient)


def test_key_creation_does_not_replace_existing_key(tmp_path):
    workspace = Workspace(tmp_path)
    path = create_key(workspace, "tessera.key")
    original = path.read_bytes()
    assert len(original) == 32
    with pytest.raises(FileExistsError):
        create_key(workspace, "tessera.key")
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "updates",
    [
        {"schema_version": True},
        {"algorithm": "none"},
        {"nonce": "bad!"},
        {"ciphertext": ""},
    ],
)
def test_invalid_packet_schema_refused(updates):
    packet = json.loads(seal(b"payload", b"x" * 32, "agent-a"))
    packet.update(updates)
    with pytest.raises(ValueError):
        unseal(json.dumps(packet).encode(), b"x" * 32, "agent-a")
