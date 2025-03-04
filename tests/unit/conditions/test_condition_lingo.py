import pytest

import nucypher
from nucypher.blockchain.eth.constants import NULL_ADDRESS
from nucypher.policy.conditions.context import USER_ADDRESS_CONTEXT
from nucypher.policy.conditions.exceptions import InvalidConditionLingo
from nucypher.policy.conditions.lingo import ConditionLingo


@pytest.fixture(scope='module')
def lingo():
    return [
        {"returnValueTest": {"value": 0, "comparator": ">"}, "method": "timelock"},
        {"operator": "and"},
        {"returnValueTest": {"value": 99999999999999999, "comparator": "<"}, "method": "timelock"},
    ]


def test_invalid_condition():
    with pytest.raises(InvalidConditionLingo):
        ConditionLingo.from_list([{}])

    with pytest.raises(InvalidConditionLingo):
        ConditionLingo.from_list([{"dont_mind_me": "nothing_to_see_here"}])

    # operator in incorrect spot
    invalid_operator_position_lingo = [
        {"operator": "and"},
        {"returnValueTest": {"value": 0, "comparator": ">"}, "method": "timelock"},
    ]
    with pytest.raises(InvalidConditionLingo):
        ConditionLingo.from_list(invalid_operator_position_lingo)


def test_condition_lingo_to_from_list(lingo):
    clingo = ConditionLingo.from_list(lingo)
    clingo_list = clingo.to_list()
    assert clingo_list == lingo


def test_condition_lingo_repr(lingo):
    clingo = ConditionLingo.from_list(lingo)
    clingo_string = f"{clingo}"
    assert f"{clingo.__class__.__name__}" in clingo_string
    assert f"id={clingo.id}" in clingo_string
    assert f"size={len(bytes(clingo))}" in clingo_string


def test_lingo_parameter_int_type_preservation(custom_abi_with_multiple_parameters, mocker):
    mocker.patch.dict(
        nucypher.policy.conditions.context._DIRECTIVES,
        {USER_ADDRESS_CONTEXT: lambda: NULL_ADDRESS},
    )
    clingo = ConditionLingo.from_list([custom_abi_with_multiple_parameters])
    conditions = clingo.to_list()
    assert conditions[0]["parameters"][2] == 4
