import pandas as pd

import handler
from smart_campus.simulator import FaultInjector, build_message
from test_validation import message


class FakeDb:
    def __init__(self):
        self.readings, self.rejects = [], []

    def write(self, readings, rejects):
        self.readings += readings
        self.rejects += rejects


class FakeArchive:
    def __init__(self):
        self.batches = []

    def write(self, records):
        self.batches.append(records)


def test_batch_is_archived_split_and_deduplicated():
    db, archive = FakeDb(), FakeArchive()
    dup_first = message(value=1.0)
    dup_last = message(value=2.0, message_id="0f4c1e9a-2b3d-4e5f-8a9b-0c1d2e3f4a5b")
    bad = message(value=-5)

    result = handler.handler({"records": [dup_first, dup_last, bad]}, db=db, archive=archive)

    assert result == {"received": 3, "accepted": 1, "rejected": 1}
    assert archive.batches == [[dup_first, dup_last, bad]]  # raw zone keeps everything
    assert [r.value for r in db.readings] == [2.0]  # last write wins
    assert db.rejects[0][0] == "negative_consumption"


def test_single_message_event_is_supported():
    """The IoT Rule Lambda action delivers one message per invocation."""
    db = FakeDb()
    assert handler.handler(message(), db=db)["accepted"] == 1


def test_simulator_messages_satisfy_contract():
    ts = pd.Timestamp("2017-07-01T05:00:00Z")
    msg = build_message("Lamb", "Lamb_office_Joy", "gas", ts, 3.14159)
    db = FakeDb()
    assert handler.handler(msg, db=db)["accepted"] == 1
    assert db.readings[0].ts == ts.to_pydatetime()


def test_fault_injector_stuck_meter_repeats_value():
    faults = FaultInjector(rate=0)
    faults.stuck[("Lamb_office_Joy", "gas")] = (9.9, 2)
    ts = pd.Timestamp("2017-07-01T05:00:00Z")
    values = []
    for v in (1.0, 2.0, 3.0):
        msg = build_message("Lamb", "Lamb_office_Joy", "gas", ts, v)
        values.append(faults.apply(msg)[0])
    assert [b'"value": 9.9' in p for p in values] == [True, True, False]
