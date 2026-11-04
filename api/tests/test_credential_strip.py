import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from app import schemas


def test_pasted_token_text_is_stripped_before_it_is_stored():
    c = schemas.CredentialCreate(slot_name="shell", token_user=" pyxie-shell@pve\n", token_id="shell ", token_secret="abc-123\n")
    assert (c.token_user, c.token_id, c.token_secret) == ("pyxie-shell@pve", "shell", "abc-123")
    u = schemas.CredentialUpdate(token_user="u@pve", token_id="t", token_secret="  s3cret \r\n")
    assert u.token_secret == "s3cret"
