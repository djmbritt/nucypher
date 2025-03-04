from collections import OrderedDict
from unittest.mock import Mock

import pytest
from eth_account import Account
from nucypher_core.umbral import SecretKey

from tests.mock.coordinator import MockCoordinatorAgent
from tests.mock.interfaces import MockBlockchain

DKG_SIZE = 4


@pytest.fixture(scope='module')
def nodes_transacting_powers():
    accounts = OrderedDict()
    for _ in range(DKG_SIZE):
        account = Account.create()
        mock_transacting_power = Mock()
        mock_transacting_power.account = account.address
        accounts[account.address] = mock_transacting_power
    return accounts


@pytest.fixture(scope='module')
def coordinator():
    return MockCoordinatorAgent(blockchain=MockBlockchain())


def test_mock_coordinator_creation(coordinator):
    assert len(coordinator.rituals) == 0


def test_mock_coordinator_initiation(mocker, nodes_transacting_powers, coordinator, random_address):
    assert len(coordinator.rituals) == 0
    mock_transacting_power = mocker.Mock()
    mock_transacting_power.account = random_address
    coordinator.initiate_ritual(
        providers=list(nodes_transacting_powers.keys()),
        transacting_power=mock_transacting_power,
    )
    assert len(coordinator.rituals) == 1

    assert coordinator.number_of_rituals() == 1

    ritual = coordinator.rituals[0]
    assert len(ritual.participants) == DKG_SIZE
    for p in ritual.participants:
        assert p.transcript == bytes()

    assert len(coordinator.EVENTS) == 1

    timestamp, signal = list(coordinator.EVENTS.items())[0]
    signal_type, signal_data = signal
    assert signal_type == MockCoordinatorAgent.Events.START_RITUAL
    assert signal_data["ritual_id"] == 0
    assert signal_data["initiator"] == mock_transacting_power.account
    assert set(signal_data["participants"]) == nodes_transacting_powers.keys()


def test_mock_coordinator_round_1(
    nodes_transacting_powers, coordinator, random_transcript
):
    ritual = coordinator.rituals[0]
    assert (
        coordinator.get_ritual_status(0)
        == MockCoordinatorAgent.RitualStatus.AWAITING_TRANSCRIPTS
    )

    for p in ritual.participants:
        assert p.transcript == bytes()

    for index, node_address in enumerate(nodes_transacting_powers):
        transcript = random_transcript

        coordinator.post_transcript(
            ritual_id=0,
            transcript=transcript,
            transacting_power=nodes_transacting_powers[node_address]
        )

        performance = ritual.participants[index]
        assert performance.transcript == bytes(transcript)

        if index == len(nodes_transacting_powers) - 1:
            assert len(coordinator.EVENTS) == 2

    timestamp, signal = list(coordinator.EVENTS.items())[1]
    signal_type, signal_data = signal
    assert signal_type == MockCoordinatorAgent.Events.START_AGGREGATION_ROUND
    assert signal_data["ritual_id"] == 0


def test_mock_coordinator_round_2(
    nodes_transacting_powers,
    coordinator,
    aggregated_transcript,
    dkg_public_key,
    random_transcript,
):
    ritual = coordinator.rituals[0]
    assert (
        coordinator.get_ritual_status(0)
        == MockCoordinatorAgent.RitualStatus.AWAITING_AGGREGATIONS
    )

    for p in ritual.participants:
        assert p.transcript == bytes(random_transcript)

    request_encrypting_keys = []
    for index, node_address in enumerate(nodes_transacting_powers):
        request_encrypting_key = SecretKey.random().public_key()
        coordinator.post_aggregation(
            ritual_id=0,
            aggregated_transcript=aggregated_transcript,
            public_key=dkg_public_key,
            request_encrypting_key=request_encrypting_key,
            transacting_power=nodes_transacting_powers[node_address]
        )
        request_encrypting_keys.append(request_encrypting_key)
        if index == len(nodes_transacting_powers) - 1:
            assert len(coordinator.EVENTS) == 2

    assert ritual.aggregated_transcript == bytes(aggregated_transcript)

    assert bytes(ritual.public_key) == bytes(dkg_public_key)
    for index, p in enumerate(ritual.participants):
        # unchanged
        assert p.transcript == bytes(random_transcript)
        assert p.transcript != bytes(aggregated_transcript)
        assert (
            p.requestEncryptingKey
            == request_encrypting_keys[index].to_compressed_bytes()
        )

    assert len(coordinator.EVENTS) == 2  # no additional event emitted here?
    assert (
        coordinator.get_ritual_status(0) == MockCoordinatorAgent.RitualStatus.FINALIZED
    )
