from typing import Dict, List, Tuple

from eth_typing import ChecksumAddress
from nucypher_core import (
    EncryptedThresholdDecryptionRequest,
    EncryptedThresholdDecryptionResponse,
)

from nucypher.network.client import ThresholdAccessControlClient
from nucypher.utilities.concurrency import BatchValueFactory, WorkerPool


class ThresholdDecryptionClient(ThresholdAccessControlClient):
    class ThresholdDecryptionRequestFailed(Exception):
        """Raised when a decryption request returns a non-zero status code."""

    class ThresholdDecryptionRequestFactory(BatchValueFactory):
        def __init__(self, ursula_to_contact: List[ChecksumAddress], threshold: int):
            # TODO should we batch the ursulas to contact i.e. pass `batch_size` parameter
            super().__init__(values=ursula_to_contact, required_successes=threshold)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def gather_encrypted_decryption_shares(
        self,
        encrypted_requests: Dict[ChecksumAddress, EncryptedThresholdDecryptionRequest],
        threshold: int,
        timeout: float = 10,
    ) -> Tuple[
        Dict[ChecksumAddress, EncryptedThresholdDecryptionResponse],
        Dict[ChecksumAddress, str],
    ]:
        self._ensure_ursula_availability(
            ursulas=list(encrypted_requests.keys()),
            threshold=threshold,
            timeout=timeout,
        )

        def worker(
            ursula_address: ChecksumAddress,
        ) -> EncryptedThresholdDecryptionResponse:
            encrypted_request = encrypted_requests[ursula_address]

            try:
                node_or_sprout = self._learner.known_nodes[ursula_address]
                node_or_sprout.mature()
                response = (
                    self._learner.network_middleware.get_encrypted_decryption_share(
                        node_or_sprout, bytes(encrypted_request)
                    )
                )
                if response.status_code == 200:
                    return EncryptedThresholdDecryptionResponse.from_bytes(
                        response.content
                    )
            except Exception as e:
                self.log.warn(f"Node {ursula_address} raised {e}")
                raise

            message = f"Node {ursula_address} returned {response.status_code} - {response.content}."
            self.log.warn(message)
            raise self.ThresholdDecryptionRequestFailed(message)

        worker_pool = WorkerPool(
            worker=worker,
            value_factory=self.ThresholdDecryptionRequestFactory(
                ursula_to_contact=list(encrypted_requests.keys()), threshold=threshold
            ),
            target_successes=threshold,
            timeout=timeout,
        )
        worker_pool.start()
        try:
            successes = worker_pool.block_until_target_successes()
        except (WorkerPool.OutOfValues, WorkerPool.TimedOut):
            # It's possible to raise some other exceptions here but we will use the logic below.
            successes = worker_pool.get_successes()
        finally:
            worker_pool.cancel()
            worker_pool.join()
        failures = worker_pool.get_failures()

        return successes, failures
