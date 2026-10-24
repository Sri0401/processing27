import pytest

from validation import ValidationError, validate


def message(**overrides):
    msg = {
        "message_id": "7d0b1c3e-7a51-4b8e-9a5f-2b0c1f0f8a10",
        "site_id": "Lamb",
        "building_id": "Lamb_education_Ora",
        "meter": "electricity",
        "ts": "2017-07-01T00:00:00Z",
        "value": 12.5,
        "unit": "kWh",
        "schema_version": 1,
    }
    msg.update(overrides)
    return msg


def test_valid_message_parses():
    reading = validate(message())
    assert reading.value == 12.5
    assert reading.ts.isoformat() == "2017-07-01T00:00:00+00:00"
    assert reading.key == ("Lamb_education_Ora", "electricity", reading.ts)


def test_zero_consumption_is_valid():
    assert validate(message(value=0)).value == 0.0


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"value": -1}, "negative_consumption"),
        ({"value": "12"}, "value_not_numeric"),
        ({"value": True}, "value_not_numeric"),
        ({"value": float("nan")}, "value_not_finite"),
        ({"value": 10_000_000}, "value_out_of_range"),
        ({"ts": "2017-07-01T00:00:00"}, "timestamp_without_timezone"),
        ({"ts": "yesterday"}, "invalid_timestamp"),
        ({"meter": "plutonium"}, "unknown_meter"),
        ({"unit": "MWh"}, "unsupported_unit"),
        ({"schema_version": 2}, "unsupported_schema_version"),
        ({"message_id": "not-a-uuid"}, "invalid_message_id"),
        ({"building_id": "Fox_office_Joy"}, "building_not_in_site"),
        ({"ts": None}, "missing_field:ts"),
    ],
)
def test_contract_violations_are_rejected(overrides, reason):
    with pytest.raises(ValidationError, match=reason):
        validate(message(**overrides))


def test_non_object_payload_is_rejected():
    with pytest.raises(ValidationError, match="payload_not_object"):
        validate(["not", "an", "object"])


def test_unparseable_payload_is_rejected():
    from smart_campus.bridge import decode

    with pytest.raises(ValidationError, match="unparseable_json"):
        validate(decode(b'{"value": '))
