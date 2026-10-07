"""The gallery's config may only ever hold public Supabase values."""
import base64
import json

import pytest

import deploy_space


def jwt(role):
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.signature"


def test_public_key_makes_the_gallery_config():
    js = deploy_space.public_config("https://abc.supabase.co/", "sb_publishable_xyz")
    assert js == 'window.CREATIVE_LENS = {"supabaseUrl": "https://abc.supabase.co", "publishableKey": "sb_publishable_xyz"};\n'


def test_older_anon_keys_are_public_too():
    assert jwt("anon") in deploy_space.public_config("https://abc.supabase.co", jwt("anon"))


@pytest.mark.parametrize("key", ["sb_secret_abc", jwt("service_role")])
def test_secret_keys_are_refused(key):
    with pytest.raises(deploy_space.DeployError, match="SECRET"):
        deploy_space.public_config("https://abc.supabase.co", key)


def test_missing_values_are_explained():
    with pytest.raises(deploy_space.DeployError, match="must both be filled in"):
        deploy_space.public_config("", "sb_publishable_xyz")
