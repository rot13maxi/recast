#!/usr/bin/env python3
"""Integration checks against a running prod VM, with supervisor STOPPED.

Run: docker compose stop supervisor; python3 test/auto_repair.py
Restores the vending method and automatic-repair setting afterward. Edits and
incident history remain tentative; use a disposable data directory for CI.
"""
import json
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
OPERATION = '| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense'
GOOD = "VendingMachine compile: 'dispense paid >= price ifFalse: [ self error: ''insufficient credit'' ]. paid := paid - price. ^ ''product'''"


def cli(*args):
    return subprocess.check_output([str(ROOT / 'lk'), *args], text=True, timeout=15).strip()


def incidents():
    return json.loads(cli('incidents'))


def trigger():
    message = 'integration-' + uuid.uuid4().hex
    cli('eval', "VendingMachine compile: 'dispense self error: ''" + message + "'''. nil")
    start = time.monotonic()
    assert 'ERROR:' in cli('eval', OPERATION)
    assert time.monotonic() - start < 3, 'failing call blocked on investigation'
    incident = incidents()[-1]
    assert incident['status'] == 'awaiting-model', incident
    request = incident['request']
    assert request['selector'] == 'dispense', request
    assert request['state'] == {'price': '50', 'paid': '60'}, request
    assert 'perform: #dispense' in request['repro']
    return incident


def respond(incident, **answer):
    path = ROOT / 'data/heal' / f'resp-{incident["id"]}.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'id': incident['id'], **answer}))
    temporary.replace(path)


def wait_for(incident, status):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        latest = next(i for i in incidents() if i['id'] == incident['id'])
        if latest['status'] == status:
            return latest
        assert latest['status'] in ('awaiting-model', 'verifying'), latest
        time.sleep(.1)
    raise AssertionError(f'No {status} verdict for incident {incident["id"]}')


def check():
    previous = json.loads(cli('status'))['automaticRepair']
    original = cli('eval', '(VendingMachine >> #dispense) sourceCode')
    original_source = original[1:-1].replace("''", "'")
    try:
        cli('autorepair', 'on')
        incident = trigger()
        before = len(incidents())
        cli('eval', OPERATION)
        assert len(incidents()) == before, 'duplicate incident queued'
        assert json.loads(cli('status'))['alive'], 'queue unavailable while awaiting model'
        respond(incident, decision='repair', reason='Fixture sabotage.', source="VendingMachine compile: 'dispense ^ ''SABOTAGED'''")
        rejected = wait_for(incident, 'shadow-verify-failed')
        assert rejected['detail']['ok'] is False
        assert 'ERROR:' in cli('eval', OPERATION), 'rejected candidate touched live method'
        assert len(incidents()) == before, 'rejected incident retried itself'
        print('PASS: capture, responsiveness, deduplication, shadow rejection')

        incident = trigger()
        respond(incident, decision='repair', reason='Wrong target.', source="Object compile: 'dispense ^ 1'")
        wait_for(incident, 'out-of-scope')
        print('PASS: out-of-scope patch rejected')

        for decision in ('expected', 'insufficient-evidence'):
            incident = trigger()
            respond(incident, decision=decision, reason='No change justified.')
            wait_for(incident, decision)
            assert 'ERROR:' in cli('eval', OPERATION)
        print('PASS: model can decline to repair')

        incident = trigger()
        cli('eval', GOOD)
        respond(incident, decision='repair', reason='Stale fixture.', source="VendingMachine compile: 'dispense ^ ''SABOTAGED'''")
        states = {i['id']: i['status'] for i in incidents()}
        assert states[incident['id']] == 'stale', states
        assert cli('eval', OPERATION) == "'product'"
        print('PASS: stale investigation cannot overwrite a newer edit')

        incident = trigger()
        slow_good = GOOD.replace("'dispense ", "'dispense (Delay forSeconds: 2) wait. ")
        respond(incident, decision='repair', reason='Restore contract.', source=slow_good)
        wait_for(incident, 'verifying')
        start = time.monotonic()
        assert json.loads(cli('status'))['alive']
        assert cli('eval', '1 + 1') == '2'
        assert time.monotonic() - start < 2, 'shadow verification blocked queue'
        repaired = wait_for(incident, 'repaired')
        assert repaired['detail']['repro'] == 'true'
        assert cli('eval', OPERATION) == "'product'"
        assert json.loads(cli('tests'))['ok']
        print('PASS: asynchronous shadow verification and live swap')

        before = len(incidents())
        assert 'insufficient credit' in cli('eval', 'VendingMachine make dispense')
        assert 'ERROR:' in cli('eval', 'ThisClassDoesNotExist new')
        cli('tests')
        assert len(incidents()) == before
        print('PASS: expected rejection, compile errors, and tests do not enqueue repairs')

        incident = trigger()
        cli('autorepair', 'off')
        wait_for(incident, 'cancelled')
        respond(incident, decision='repair', reason='Too late.', source=GOOD)
        assert 'ERROR:' in cli('eval', OPERATION)
        print('PASS: disabling repair cancels pending work')
    finally:
        cli('autorepair', 'off')
        cli('eval', "VendingMachine compile: '" + original_source.replace("'", "''") + "'")
        cli('autorepair', 'on' if previous else 'off')


if __name__ == '__main__':
    check()
