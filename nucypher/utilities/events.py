"""
Credit to the original authors for making this code example incorporated into this module found here:
https://web3py.readthedocs.io/en/stable/examples.html#example-code

A stateful event scanner for Ethereum-based blockchains using Web3.py.

With the stateful mechanism, you can do one batch scan or incremental scans,
where events are added wherever the scanner left off.
"""

import json

import time

import datetime

from abc import ABC, abstractmethod

import logging

import csv
from collections import OrderedDict

from eth_abi.codec import ABICodec
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Callable, Iterable

import maya
from web3 import Web3
from web3._utils.events import get_event_data
from web3._utils.filters import construct_event_filter_params
from web3.contract.contract import Contract
from web3.datastructures import AttributeDict
from web3.exceptions import BlockNotFound
from web3.types import BlockIdentifier

from nucypher.blockchain.eth.agents import EthereumContractAgent
from nucypher.blockchain.eth.events import EventRecord


def generate_events_csv_filepath(contract_name: str, event_name: str) -> Path:
    return Path(f'{contract_name}_{event_name}_{maya.now().datetime().strftime("%Y-%m-%d_%H-%M-%S")}.csv')


def write_events_to_csv_file(csv_file: Path,
                             agent: EthereumContractAgent,
                             event_name: str,
                             argument_filters: Dict = None,
                             from_block: Optional[BlockIdentifier] = 0,
                             to_block: Optional[BlockIdentifier] = 'latest') -> bool:
    """
    Write events to csv file.
    :return: True if data written to file, False if there was no event data to write
    """
    event_type = agent.contract.events[event_name]
    entries = event_type.get_logs(fromBlock=from_block, toBlock=to_block, argument_filters=argument_filters)
    if not entries:
        return False

    with open(csv_file, mode='w') as events_file:
        events_writer = None
        for event_record in entries:
            event_record = EventRecord(event_record)
            event_row = OrderedDict()
            event_row['event_name'] = event_name
            event_row['block_number'] = event_record.block_number
            event_row['unix_timestamp'] = event_record.timestamp
            event_row['date'] = maya.MayaDT(event_record.timestamp).iso8601()
            event_row.update(dict(event_record.args.items()))
            if events_writer is None:
                events_writer = csv.DictWriter(events_file, fieldnames=event_row.keys())
                events_writer.writeheader()
            events_writer.writerow(event_row)
    return True


logger = logging.getLogger(__name__)


class EventScannerState(ABC):
    """
    Application state that remembers what blocks we have scanned in the case of crash.
    """

    @abstractmethod
    def get_last_scanned_block(self) -> int:
        """Number of the last block we have scanned on the previous cycle.

        :return: 0 if no blocks scanned yet
        """

    @abstractmethod
    def start_chunk(self, block_number: int):
        """Scanner is about to ask data of multiple blocks over JSON-RPC.

        Start a database session if needed.
        """

    @abstractmethod
    def end_chunk(self, block_number: int):
        """Scanner finished a number of blocks.

        Persistent any data in your state now.
        """

    @abstractmethod
    def process_event(self, block_when: datetime.datetime, event: AttributeDict) -> object:
        """Process incoming events.

        This function takes raw events from Web3, transforms them to your application internal
        format, then saves them in a database or some other state.

        :param block_when: When this block was mined

        :param event: Symbolic dictionary of the event data

        :return: Internal state structure that is the result of event tranformation.
        """

    @abstractmethod
    def delete_data(self, since_block: int) -> int:
        """Delete any data since this block was scanned.

        Purges any potential minor reorg data.
        """


class EventScanner:
    """Scan blockchain for events and try not to abuse JSON-RPC API too much.

    Can be used for real-time scans, as it detects minor chain reorganisation and rescans.
    Unlike the easy web3.contract.Contract, this scanner can scan events from multiple contracts at once.
    For example, you can get all transfers from all tokens in the same scan.

    You *should* disable the default `http_retry_request_middleware` on your provider for Web3,
    because it cannot correctly throttle and decrease the `eth_get_logs` block number range.
    """

    def __init__(self, web3: Web3, contract: Contract, state: EventScannerState, events: List, filters: {},
                 max_chunk_scan_size: int = 10000, max_request_retries: int = 30, request_retry_seconds: float = 3.0):
        """
        :param contract: Contract
        :param events: List of web3 Event we scan
        :param filters: Filters passed to get_logs
        :param max_chunk_scan_size: JSON-RPC API limit in the number of blocks we query. (Recommendation: 10,000 for mainnet, 500,000 for testnets)
        :param max_request_retries: How many times we try to reattempt a failed JSON-RPC call
        :param request_retry_seconds: Delay between failed requests to let JSON-RPC server to recover
        """

        self.logger = logger
        self.contract = contract
        self.web3 = web3
        self.state = state
        self.events = events
        self.filters = filters

        # Our JSON-RPC throttling parameters
        self.min_scan_chunk_size = 10  # 12 s/block = 120 seconds period
        self.max_scan_chunk_size = max_chunk_scan_size
        self.max_request_retries = max_request_retries
        self.request_retry_seconds = request_retry_seconds

        # Factor how fast we increase the chunk size if results are found
        # # (slow down scan after starting to get hits)
        self.chunk_size_decrease = 0.5

        # Factor how was we increase chunk size if no results found
        self.chunk_size_increase = 2.0

    @property
    def address(self):
        return self.contract.address

    def get_block_timestamp(self, block_num) -> datetime.datetime:
        """Get Ethereum block timestamp"""
        try:
            block_info = self.web3.eth.get_block(block_num)
        except BlockNotFound:
            # Block was not mined yet,
            # minor chain reorganisation?
            return None
        last_time = block_info["timestamp"]
        return datetime.datetime.utcfromtimestamp(last_time)

    def get_suggested_scan_start_block(self):
        """Get where we should start to scan for new token events.

        If there are no prior scans, start from block 1.
        Otherwise, start from the last end block minus ten blocks.
        We rescan the last ten scanned blocks in the case there were forks to avoid
        misaccounting due to minor single block works (happens once in a hour in Ethereum).
        These heurestics could be made more robust, but this is for the sake of simple reference implementation.
        """

        end_block = self.get_last_scanned_block()
        if end_block:
            return max(1, end_block - 10)
        return 1

    def get_suggested_scan_end_block(self):
        """Get the last mined block on Ethereum chain we are following."""

        # Do not scan all the way to the final block, as this
        # block might not be mined yet
        return self.web3.eth.block_number - 1

    def get_last_scanned_block(self) -> int:
        return self.state.get_last_scanned_block()

    def delete_potentially_forked_block_data(self, after_block: int):
        """Purge old data in the case of blockchain reorganisation."""
        self.state.delete_data(after_block)

    def scan_chunk(self, start_block, end_block) -> Tuple[int, datetime.datetime, list]:
        """Read and process events between to block numbers.

        Dynamically decrease the size of the chunk if the case JSON-RPC server pukes out.

        :return: tuple(actual end block number, when this block was mined, processed events)
        """

        block_timestamps = {}
        get_block_timestamp = self.get_block_timestamp

        # Cache block timestamps to reduce some RPC overhead
        # Real solution might include smarter models around block
        def get_block_when(block_num):
            if block_num not in block_timestamps:
                block_timestamps[block_num] = get_block_timestamp(block_num)
            return block_timestamps[block_num]

        all_processed = []

        for event_type in self.events:

            # Callable that takes care of the underlying web3 call
            def _fetch_events(_start_block, _end_block):
                return _fetch_events_for_all_contracts(self.web3,
                                                       event_type,
                                                       self.filters,
                                                       from_block=_start_block,
                                                       to_block=_end_block)

            # Do `n` retries on `eth_get_logs`,
            # throttle down block range if needed
            end_block, events = _retry_web3_call(
                _fetch_events,
                start_block=start_block,
                end_block=end_block,
                retries=self.max_request_retries,
                delay=self.request_retry_seconds)

            for evt in events:
                processed = self.process_event(event=evt, get_block_when=get_block_when)
                all_processed.append(processed)

        end_block_timestamp = get_block_when(end_block)
        return end_block, end_block_timestamp, all_processed

    def process_event(self, event, get_block_when):
        """Process events and update internal state"""
        idx = event["logIndex"]  # Integer of the log index position in the block, null when its pending

        # We cannot avoid minor chain reorganisations, but
        # at least we must avoid blocks that are not mined yet
        assert idx is not None, "Somehow tried to scan a pending block"

        block_number = event["blockNumber"]

        # Get UTC time when this event happened (block mined timestamp)
        # from our in-memory cache
        block_when = get_block_when(block_number)

        logger.debug("Processing event %s, block:%d count:%d", event["event"], event["blockNumber"])
        processed = self.state.process_event(block_when, event)
        return processed

    def estimate_next_chunk_size(self, current_chuck_size: int, event_found_count: int):
        """Try to figure out optimal chunk size

        Our scanner might need to scan the whole blockchain for all events

        * We want to minimize API calls over empty blocks

        * We want to make sure that one scan chunk does not try to process too many entries once, as we try to control commit buffer size and potentially asynchronous busy loop

        * Do not overload node serving JSON-RPC API by asking data for too many events at a time

        Currently Ethereum JSON-API does not have an API to tell when a first event occured in a blockchain
        and our heuristics try to accelerate block fetching (chunk size) until we see the first event.

        These heurestics exponentially increase the scan chunk size depending on if we are seeing events or not.
        When any transfers are encountered, we are back to scanning only a few blocks at a time.
        It does not make sense to do a full chain scan starting from block 1, doing one JSON-RPC call per 20 blocks.
        """

        if event_found_count > 0:
            # When we encounter first events, reset the chunk size window
            current_chuck_size = self.min_scan_chunk_size
        else:
            current_chuck_size *= self.chunk_size_increase

        current_chuck_size = max(self.min_scan_chunk_size, current_chuck_size)
        current_chuck_size = min(self.max_scan_chunk_size, current_chuck_size)
        return int(current_chuck_size)

    def scan(self, start_block, end_block, start_chunk_size=20, progress_callback=Optional[Callable]) -> Tuple[
        list, int]:
        """Perform a token balances scan.

        Assumes all balances in the database are valid before start_block (no forks sneaked in).

        :param start_block: The first block included in the scan

        :param end_block: The last block included in the scan

        :param start_chunk_size: How many blocks we try to fetch over JSON-RPC on the first attempt

        :param progress_callback: If this is an UI application, update the progress of the scan

        :return: [All processed events, number of chunks used]
        """

        if start_block > end_block:
            start_block = end_block - 1

        current_block = start_block

        # Scan in chunks, commit between
        chunk_size = start_chunk_size
        last_scan_duration = last_logs_found = 0
        total_chunks_scanned = 0

        # All processed entries we got on this scan cycle
        all_processed = []

        while current_block <= end_block:

            self.state.start_chunk(current_block, chunk_size)

            # Print some diagnostics to logs to try to fiddle with real world JSON-RPC API performance
            estimated_end_block = current_block + chunk_size
            logger.debug(
                "Scanning token transfers for blocks: %d - %d, chunk size %d, last chunk scan took %f, last logs found %d",
                current_block, estimated_end_block, chunk_size, last_scan_duration, last_logs_found)

            start = time.time()
            actual_end_block, end_block_timestamp, new_entries = self.scan_chunk(current_block, estimated_end_block)

            # Where does our current chunk scan ends - are we out of chain yet?
            current_end = actual_end_block

            last_scan_duration = time.time() - start
            all_processed += new_entries

            # Try to guess how many blocks to fetch over `eth_get_logs` API next time
            chunk_size = self.estimate_next_chunk_size(chunk_size, len(new_entries))

            # Set where the next chunk starts
            current_block = current_end + 1
            total_chunks_scanned += 1
            self.state.end_chunk(current_end)

        return all_processed, total_chunks_scanned


def _retry_web3_call(func, start_block, end_block, retries, delay) -> Tuple[int, list]:
    """A custom retry loop to throttle down block range.

    If our JSON-RPC server cannot serve all incoming `eth_get_logs` in a single request,
    we retry and throttle down block range for every retry.

    For example, Go Ethereum does not indicate what is an acceptable response size.
    It just fails on the server-side with a "context was cancelled" warning.

    :param func: A callable that triggers Ethereum JSON-RPC, as func(start_block, end_block)
    :param start_block: The initial start block of the block range
    :param end_block: The initial start block of the block range
    :param retries: How many times we retry
    :param delay: Time to sleep between retries
    """
    for i in range(retries):
        try:
            return end_block, func(start_block, end_block)
        except Exception as e:
            # Assume this is HTTPConnectionPool(host='localhost', port=8545): Read timed out. (read timeout=10)
            # from Go Ethereum. This translates to the error "context was cancelled" on the server side:
            # https://github.com/ethereum/go-ethereum/issues/20426
            if i < retries - 1:
                # Give some more verbose info than the default middleware
                logger.warning(
                    "Retrying events for block range %d - %d (%d) failed with %s, retrying in %s seconds",
                    start_block,
                    end_block,
                    end_block-start_block,
                    e,
                    delay)
                # Decrease the `eth_get_blocks` range
                end_block = start_block + ((end_block - start_block) // 2)
                # Let the JSON-RPC to recover e.g. from restart
                time.sleep(delay)
                continue
            else:
                logger.warning("Out of retries")
                raise


def _fetch_events_for_all_contracts(
        web3,
        event,
        argument_filters: dict,
        from_block: int,
        to_block: int) -> Iterable:
    """Get events using eth_get_logs API.

    This method is detached from any contract instance.

    This is a stateless method, as opposed to createFilter.
    It can be safely called against nodes which do not provide `eth_newFilter` API, like Infura.
    """

    if from_block is None:
        raise TypeError("Missing mandatory keyword argument to get_logs: fromBlock")

    # Currently no way to poke this using a public Web3.py API.
    # This will return raw underlying ABI JSON object for the event
    abi = event._get_event_abi()

    # Depending on the Solidity version used to compile
    # the contract that uses the ABI,
    # it might have Solidity ABI encoding v1 or v2.
    # We just assume the default that you set on Web3 object here.
    # More information here https://eth-abi.readthedocs.io/en/latest/index.html
    codec: ABICodec = web3.codec

    # Here we need to poke a bit into Web3 internals, as this
    # functionality is not exposed by default.
    # Construct JSON-RPC raw filter presentation based on human readable Python descriptions
    # Namely, convert event names to their keccak signatures
    # More information here:
    # https://github.com/ethereum/web3.py/blob/e176ce0793dafdd0573acc8d4b76425b6eb604ca/web3/_utils/filters.py#L71
    data_filter_set, event_filter_params = construct_event_filter_params(
        abi,
        codec,
        address=argument_filters.get("address"),
        argument_filters=argument_filters,
        fromBlock=from_block,
        toBlock=to_block
    )

    logger.debug("Querying eth_get_logs with the following parameters: %s", event_filter_params)

    # Call JSON-RPC API on your Ethereum node.
    # get_logs() returns raw AttributedDict entries
    logs = web3.eth.get_logs(event_filter_params)

    # Convert raw binary data to Python proxy objects as described by ABI
    all_events = []
    for log in logs:
        # Convert raw JSON-RPC log result to human readable event by using ABI data
        # More information how processLog works here
        # https://github.com/ethereum/web3.py/blob/fbaf1ad11b0c7fac09ba34baff2c256cffe0a148/web3/_utils/events.py#L200
        evt = get_event_data(codec, abi, log)
        # Note: This was originally yield,
        # but deferring the timeout exception caused the throttle logic not to work
        all_events.append(evt)
    return all_events


class JSONifiedState(EventScannerState):
    """Store the state of scanned blocks and all events.

    All state is an in-memory dict.
    Simple load/store massive JSON on start up.
    """

    STATE_FILENAME = "eventscanner.json"

    def __init__(self, persistent=True):
        self.state = None
        self.fname = self.STATE_FILENAME
        # How many second ago we saved the JSON file
        self.last_save = 0
        self.persistent = persistent

    def reset(self):
        """Create initial state of nothing scanned."""
        self.state = {
            "last_scanned_block": 0,
            "blocks": {},
        }

    def restore(self):
        """Restore the last scan state from a file."""
        try:
            self.state = json.load(open(self.fname, "rt"))
            print(f"Restored the state, previously {self.state['last_scanned_block']} blocks have been scanned")
        except (IOError, json.decoder.JSONDecodeError):
            print("State starting from scratch")
            self.reset()

    def save(self):
        """Save everything we have scanned so far in a file."""
        with open(self.fname, "wt") as f:
            json.dump(self.state, f)
        self.last_save = time.time()

    #
    # EventScannerState methods implemented below
    #

    def get_last_scanned_block(self):
        """The number of the last block we have stored."""
        return self.state["last_scanned_block"]

    def delete_data(self, since_block):
        """Remove potentially reorganised blocks from the scan data."""
        for block_num in range(since_block, self.get_last_scanned_block()):
            if block_num in self.state["blocks"]:
                del self.state["blocks"][block_num]

    def start_chunk(self, block_number, chunk_size):
        pass

    def end_chunk(self, block_number):
        """Save at the end of each block, so we can resume in the case of a crash or CTRL+C"""
        # Next time the scanner is started we will resume from this block
        self.state["last_scanned_block"] = block_number

        # Save the database file for every minute
        if self.persistent and (time.time() - self.last_save > 60):
            self.save()

    def process_event(self, block_when: datetime.datetime, event: AttributeDict) -> str:
        """Record a ERC-20 event_record in our database."""
        # Events are keyed by their transaction hash and log index
        # One transaction may contain multiple events
        # and each one of those gets their own log index

        event_name = event.event # "Transfer"
        log_index = event.logIndex  # Log index within the block
        transaction_index = event.transactionIndex  # Transaction index within the block
        txhash = event.transactionHash.hex()  # Transaction hash
        block_number = event.blockNumber

        # Convert event to our internal format
        event_record = {
            "event": event_name,
            "blockTimestamp": block_when.timestamp(),
            "logIndex": log_index,
            "transactionIndex": transaction_index,
            "txhash": txhash,
            "blockNumber": block_number,
        }

        # Create empty dict as the block that contains all transactions by txhash
        if block_number not in self.state["blocks"]:
            self.state["blocks"][block_number] = {}

        block = self.state["blocks"][block_number]
        if txhash not in block:
            # We have not yet recorded any transfers in this transaction
            # (One transaction may contain multiple events if executed by a smart contract).
            # Create a tx entry that contains all events by a log index
            self.state["blocks"][block_number][txhash] = {}

        # Record event in our database
        if log_index in self.state["blocks"][block_number][txhash]:
            return None  # We have already recorded this event
        self.state["blocks"][block_number][txhash][log_index] = event_record

        # Return a pointer that allows us to look up this event later if needed
        return f"{block_number}-{txhash}-{log_index}"
