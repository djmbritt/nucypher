from typing import Iterable, List, Optional

from eth_typing import ChecksumAddress

from nucypher.blockchain.eth.agents import (
    PREApplicationAgent,
    StakingProvidersReservoir,
)


def make_staking_provider_reservoir(
    application_agent: PREApplicationAgent,
    exclude_addresses: Optional[Iterable[ChecksumAddress]] = None,
    include_addresses: Optional[Iterable[ChecksumAddress]] = None,
    pagination_size: Optional[int] = None,
):
    """Get a sampler object containing the currently registered staking providers."""

    # needs to not include both exclude and include addresses
    # so that they aren't included in reservoir, include_address will be re-added to reservoir afterwards
    include_addresses = include_addresses or ()
    without_set = set(include_addresses) | set(exclude_addresses or ())
    try:
        reservoir = application_agent.get_staking_provider_reservoir(without=without_set, pagination_size=pagination_size)
    except PREApplicationAgent.NotEnoughStakingProviders:
        # TODO: do that in `get_staking_provider_reservoir()`?
        reservoir = StakingProvidersReservoir({})

    # add include addresses
    return MergedReservoir(include_addresses, reservoir)


class MergedReservoir:
    """
    A reservoir made of a list of addresses and a StakingProviderReservoir.
    Draws the values from the list first, then from StakingProviderReservoir,
    then returns None on subsequent calls.
    """

    def __init__(self, values: Iterable, reservoir: StakingProvidersReservoir):
        self.values = list(values)
        self.reservoir = reservoir

    def __call__(self) -> Optional[ChecksumAddress]:
        if self.values:
            return self.values.pop(0)
        elif len(self.reservoir) > 0:
            return self.reservoir.draw(1)[0]
        else:
            return None


class PrefetchStrategy:
    """
    Encapsulates the batch draw strategy from a reservoir.
    Determines how many values to draw based on the number of values
    that have already led to successes.
    """

    def __init__(self, reservoir: MergedReservoir, need_successes: int):
        self.reservoir = reservoir
        self.need_successes = need_successes

    def __call__(self, successes: int) -> Optional[List[ChecksumAddress]]:
        batch = []
        for i in range(self.need_successes - successes):
            value = self.reservoir()
            if value is None:
                break
            batch.append(value)
        if not batch:
            return None
        return batch
