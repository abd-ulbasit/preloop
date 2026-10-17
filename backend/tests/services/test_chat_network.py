"""Mattermost DNS pinning reuses the instance private-network policy."""

import ipaddress
import pytest
from preloop.services.chat_network import pinned_mattermost_origin
from preloop.services.chat_providers import ChatProviderError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.4", "169.254.169.254", "::1", "::ffff:127.0.0.1"]
)
async def test_blocks_private_and_metadata_by_default(
    monkeypatch, address: str
) -> None:
    from preloop.config import settings

    monkeypatch.setattr(settings, "model_endpoint_allowed_cidrs", "")
    monkeypatch.setattr(
        "preloop.services.ai_model_provider._resolve_endpoint_host",
        lambda _: [ipaddress.ip_address(address)],
    )
    with pytest.raises(ChatProviderError, match="outbound network policy"):
        await pinned_mattermost_origin("https://chat.example.com")


@pytest.mark.asyncio
async def test_private_intranet_opt_in_pins_ip_preserving_host_and_sni(
    monkeypatch,
) -> None:
    from preloop.config import settings

    monkeypatch.setattr(settings, "model_endpoint_allowed_cidrs", "10.0.0.0/24")
    monkeypatch.setattr(
        "preloop.services.ai_model_provider._resolve_endpoint_host",
        lambda _: [ipaddress.ip_address("10.0.0.4")],
    )
    assert await pinned_mattermost_origin("https://chat.example.com:8443") == (
        "https://10.0.0.4:8443",
        "chat.example.com:8443",
        "chat.example.com",
    )


@pytest.mark.asyncio
async def test_any_private_dns_answer_blocks_public_private_mix(monkeypatch) -> None:
    from preloop.config import settings

    monkeypatch.setattr(settings, "model_endpoint_allowed_cidrs", "")
    monkeypatch.setattr(
        "preloop.services.ai_model_provider._resolve_endpoint_host",
        lambda _: [
            ipaddress.ip_address("93.184.216.34"),
            ipaddress.ip_address("10.0.0.4"),
        ],
    )
    with pytest.raises(ChatProviderError):
        await pinned_mattermost_origin("https://chat.example.com")
