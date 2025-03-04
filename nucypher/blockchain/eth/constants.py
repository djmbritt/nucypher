

#
# Contract Names
#

DISPATCHER_CONTRACT_NAME = 'Dispatcher'
NUCYPHER_TOKEN_CONTRACT_NAME = 'NuCypherToken'
STAKING_ESCROW_CONTRACT_NAME = 'StakingEscrow'
STAKING_ESCROW_STUB_CONTRACT_NAME = 'StakingEscrowStub'
ADJUDICATOR_CONTRACT_NAME = 'Adjudicator'
PRE_APPLICATION_CONTRACT_NAME = 'SimplePREApplication'  # TODO: Use the real PREApplication
SUBSCRIPTION_MANAGER_CONTRACT_NAME = 'SubscriptionManager'

NUCYPHER_CONTRACT_NAMES = (
    NUCYPHER_TOKEN_CONTRACT_NAME,
    STAKING_ESCROW_CONTRACT_NAME,
    ADJUDICATOR_CONTRACT_NAME,
    DISPATCHER_CONTRACT_NAME,
    PRE_APPLICATION_CONTRACT_NAME,
    SUBSCRIPTION_MANAGER_CONTRACT_NAME
)


# Ethereum

AVERAGE_BLOCK_TIME_IN_SECONDS = 14
ETH_ADDRESS_BYTE_LENGTH = 20
ETH_ADDRESS_STR_LENGTH = 40
ETH_HASH_BYTE_LENGTH = 32
LENGTH_ECDSA_SIGNATURE_WITH_RECOVERY = 65
MAX_UINT16 = 65535
NULL_ADDRESS = '0x' + '0' * 40

# NuCypher
# TODO: this is equal to HRAC.SIZE.
POLICY_ID_LENGTH = 16
