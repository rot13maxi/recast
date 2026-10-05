# A tour of Recast

Break a running Smalltalk application, let an ordinary failing call trigger
an LLM repair, and watch the next call succeed without a restart. Then promote
the patch and prove that it survives one.

The main tour covers [setup](#0-configure-the-llm-and-boot),
[a live edit](#1-hot-swap-a-class), [a deliberate failure](#2-break-the-app),
[automatic repair](#3-automatic-repair-from-an-ordinary-call), and
[promotion](#4-promote-and-prove-persistence). The later sections are optional
experiments and cleanup.

You can complete the tour without an API key using the
[offline fake LLM](#offline-path-use-the-fake-llm). It supplies canned patches
through the same verification path; the main tour uses a real model.
Both paths have been exercised, including real repairs from a real model.

You need Git, Docker with the Compose plugin, Python 3, and an API key for
your chosen LLM endpoint. Run the commands in Bash. The examples assume a
fresh `data/` directory; an existing one resumes its promoted definitions
and can produce different journal counts or behavior.

Get the code first:

```bash
git clone https://github.com/rot13maxi/recast.git
cd recast
```

The Smalltalk code is provided below; you do not need to know the language
to follow the tour. Define classes before referring to them in another eval,
and double single quotes inside method-source strings.

## 0. Configure the LLM and boot

The supervisor uses an OpenAI-compatible Chat Completions endpoint. The
included template uses a real model, which was used to validate
this tour. You can use another compatible provider or a local model. Create
a local configuration:

```bash
cp .env.example .env
```

Fill in `LIVE_LLM_API_KEY` in `.env`, leaving the other values as shown:

```dotenv
LIVE_LLM_URL=https://api.openai.com/v1/chat/completions
LIVE_LLM_MODEL=current
LIVE_LLM_API_KEY=your-api-key
```

Compose reads `.env` automatically. The file is ignored by Git. If `.env`
already exists, edit it rather than copying the template over it. Exported
shell variables override `.env`; unset any old `LIVE_LLM_*` exports when
switching back to this configuration.

For another provider or a local model, set the full chat-completions URL and
its model name. The supervisor sends `messages` and `temperature: 0.2` and
reads `choices[0].message.content`, so the endpoint must support that format.
For a model running on the host, use `host.docker.internal` in the URL so the
supervisor container can reach it. Leave `LIVE_LLM_API_KEY` empty if that
endpoint requires no authentication.

Build the Pharo base image once, then start the app and supervisor. The
initial build downloads Pharo and its dependencies:

```bash
docker build -f Dockerfile.base -t live-smalltalk-base:latest .
docker compose up -d --build
./lk status
```

`lk status` waits for the queue to answer, so there is no fixed boot delay to
guess. On a fresh boot, expect these fields (additional fields are omitted):

```json
{ "alive": true, "journalSize": 0, "marker": 0,
  "agentMode": "live", "testClasses": ["VendingMachineTests"] }
```

`alive` comes from the heartbeat file's age. A queue timeout means the image
has not answered; inspect `docker compose ps` and `./lk logs` before continuing.

Check the LLM connection **from the supervisor container**, using the same
client code that will handle the repair:

```bash
docker compose exec -T supervisor python - <<'PY'
from supervisor import call_llm
source = call_llm({
    'target': 'VendingMachine',
    'problem': 'Connection check only: return the Smalltalk expression nil.',
    'repro': '',
    'journalSize': 0,
})
assert source.strip(), 'Endpoint returned no source'
print('LLM connection OK; received source (not applied).')
PY
```

This makes one model request without applying the answer. Authentication,
model-access, and network errors appear here before you break the app. If
you change endpoint variables later, recreate the supervisor with
`docker compose up -d --force-recreate supervisor`.

The demo app is `VendingMachine`. Its price starts at 50; inserting enough
credit lets `dispense` return `'product'`:

```bash
./lk eval "| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense"
# -> 'product'
./lk tests
# "run": 2, "ok": true
```

The eval ran inside the live VM. Its output is the result's Smalltalk
`printString`.

## 1. Hot-swap a class

Create a class, compile a method, and call it without restarting:

```bash
./lk eval "Smalltalk globals at: #Greeting ifAbsent: [
  Object subclass: #Greeting instanceVariableNames: '' classVariableNames: ''
    poolDictionaries: '' package: 'Demo' ]. nil"

./lk eval "Greeting compile: 'greet: name
  ^ ''hello, '', name , ''!'''. nil"

./lk eval "Greeting new greet: 'world'"
# -> 'hello, world!'
```

These are separate evals because `Greeting` must exist when the expressions
that reference it compile. Inside the `compile:` string, `''hello, ''` and
`''!''` represent quoted strings in the method body. The third quote after
`!` closes the outer source string.

The new definitions are live and journalled, but remain tentative until
promotion. Restarting now would rebuild the image without them.

`./lk logs` may include `DeprecationPerformedNotification` messages from the
`Object subclass:` idiom. For boot status, look for
`LK-BOOT: replayed N entries, 0 failures`.

## 2. Break the app

Keep automatic repair off while you inspect the failure. Replace `dispense`
with a method that always raises an error:

```bash
./lk autorepair off
./lk eval "VendingMachine compile: 'dispense
  self error: ''the machine is on fire'''. nil"

./lk eval "| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense"
# -> ERROR: Error: the machine is on fire

./lk tests
# "ok": false
./lk promote
# "ok": false, "why": "refusing to promote: tests failing"
```

The edit compiles, so `lk eval` applies it immediately. The failing suite
blocks promotion. Restart would discard the unpromoted changes, including
`Greeting`. Next, let the failing call itself trigger a repair.

## 3. Automatic repair from an ordinary call

The method is still broken. Enable incident handling, then make another
ordinary application call:

```bash
./lk autorepair on
./lk eval "| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense"
# -> ERROR: Error: the machine is on fire
./lk incidents
```

The failing call returns its error and queues an investigation. The captured
request supplies the stack, application source, receiver's price and credit,
existing tests, and the `VendingMachine` repair contract. No manual problem
description or repro file is needed. The repro reconstructs the failing
receiver in the shadow VM rather than replaying the entire original eval.

Poll `./lk incidents` until the latest incident reaches `repaired`. Its status
normally moves through `awaiting-model` and `verifying`; its `detail` then
contains the shadow verdict. Both the image queue and heartbeat continue
while the model and shadow VM work:

```bash
./lk status
./lk incidents
# Latest status: repaired; detail.ok: true; detail.repro: true
./lk eval "| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense"
# -> 'product'
./lk tests
# "ok": true
```

This path has been validated with a real model. The patch remains
tentative; use `./lk promote` if you want it to survive restart.

The model may instead return `expected` or `insufficient-evidence`. Other
terminal statuses include `shadow-verify-failed`, `out-of-scope`, `stale`,
`bad-supervisor-response`, `supervisor-timeout`, `shadow-timeout`, and
`repair-error`. Inspect the incident's `detail` and its matching request and
response in `data/heal/`. An unchanged failing method is investigated once;
a new method version can produce a new incident. Restart cancels outstanding
work, and code changes during an investigation invalidate its proposal.

Automatic repair currently covers `VendingMachine` instance methods. The
class declares its expected failures and repair contract. Insufficient
credit, compiler errors, boot replay, test runs, and shadow evaluation do not
start investigations. Receivers and arguments must contain small literal
values; complex object graphs are skipped. The original failed operation is
never automatically retried.

Turn automatic repair off after the experiment. This cancels pending work
and leaves accepted patches in place:

```bash
./lk autorepair off
```

## 4. Promote and prove persistence

Promotion advances the marker to include all journalled definitions: the
`Greeting` class, its method, the broken `dispense`, and the later repair.
On replay, the repair replaces the broken method before the queue starts.

```bash
./lk promote
# "ok": true, "marker": N
docker compose restart prod
./lk status
# journalSize: N, marker: N
./lk eval "Greeting new greet: 'again'"
# -> 'hello, again!'
./lk tests
# "ok": true
```

The restarted VM loaded the base image and replayed the promoted journal.
It did not restore a saved heap snapshot.

Now try an unpromoted edit:

```bash
./lk eval "Greeting compile: 'greet: name
  ^ ''bonjour, '', name'. nil"
./lk eval "Greeting new greet: 'maintenant'"
# -> 'bonjour, maintenant'
docker compose restart prod
./lk status
./lk eval "Greeting new greet: 'again'"
# -> 'hello, again!'
```

The promoted method survives; the tentative replacement is left unapplied
on restart. The journal can still contain entries beyond the marker, so
`journalSize` may be larger than `marker`.

## 5. Optional: request a repair explicitly

You can also drive repair directly with `lk heal`. This is useful when you
already have a repro or want to supply additional context. Break the method
again for this separate experiment:

```bash
./lk autorepair off
./lk eval "VendingMachine compile: 'dispense
  self error: ''the machine is on fire'''. nil"
```

`lk heal` sends a problem description, target name, and repro to the
supervisor. The model gets those fields, but no automatic dump of the class
or its methods. Give it the fields and behavior it needs to write the fix.

Write a repro that fails on the broken method and returns `true` after a
successful repair:

```bash
cat > /tmp/repro.st <<'ST'
| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense = 'product'
ST

./lk heal --problem "VendingMachine>>dispense always raises 'the machine is on fire'.
The class has instance variables price and paid. init sets price := 50 and paid := 0.
insertCoin: adds its argument to paid. Repair only dispense: if paid < price,
raise 'insufficient credit'; otherwise subtract price from paid and return 'product'.
Return Smalltalk statements that compile the method into VendingMachine." \
  --repro /tmp/repro.st --target VendingMachine
# Success includes "ok": true. The "swapped" value depends on the source returned.
```

The supervisor asks the model for source in a fenced Smalltalk code block.
The image then starts a separate VM, replays the journal, evaluates the
candidate, and runs the repro and suite. A green verdict allows the live
image to apply the patch. The request can take a few seconds or longer,
depending on the endpoint.

Confirm the result in the live image:

```bash
./lk eval "| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense"
# -> 'product'
./lk tests
# "ok": true
./lk healed
ls data/heal/
```

`lk healed` lists successful repairs. The request and response files in
`data/heal/` preserve proposals, including rejected ones. The response also
records the model name. The repaired method is still tentative until you
promote it. Run `./lk promote` after the live checks pass if you want to
retain this repair.

If the repair fails, use the returned `stage` to find the cause:

| Stage | What to check |
|-------|---------------|
| `supervisor-timeout` | The image waited about 120 seconds for a response. Check `docker compose ps` and `docker compose logs --tail 50 supervisor`. |
| `bad-supervisor-response` | Inspect the matching `data/heal/resp-N.json` for an endpoint error, then rerun the connection check in section 0. |
| `shadow-verify-failed` | The proposed source did not pass compilation, the repro, or the suite. Inspect `source` and `shadow` in the result; clarify the problem and issue a new heal request. |
| `swap-failed` | The candidate passed in the shadow VM but failed during live evaluation. Inspect the result and live logs. |

A real model may produce a rejected proposal. Continue once the heal returns
`ok: true` and the live checks pass. The gate verifies tested behavior; it
does not establish that arbitrary model-generated code is safe.

## 6. Optional: verify a candidate and try rollback

To check a patch without changing the live image:

```bash
cat > /tmp/repro.st <<'ST'
| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense = 'product'
ST
cat > /tmp/fix.st <<'ST'
VendingMachine compile: 'dispense
  paid >= price ifFalse: [ self error: ''insufficient credit'' ].
  paid := paid - price.
  ^ ''product'''
ST
./lk candidate /tmp/fix.st --repro /tmp/repro.st
# "ok": true; live image untouched
```

Run the following only after completing the persistence checks. Rolling back
to zero removes the demo edits from the running image on its next boot;
reset also erases the journal and heal history.

```bash
./lk rollback 0
docker compose restart prod
./lk status
./lk eval "Greeting new greet: 'gone'"
# ERROR: ... Undeclared variable ... Greeting
./lk reset --yes
./lk status
# fresh image, journalSize: 0, marker: 0
```

## 7. Stop and clean up

```bash
docker compose down
```

Keep `data/` to retain the journal, promotion marker, and audit trail. A
later `docker compose up -d` rebuilds from the promoted prefix. To erase the
state after stopping the containers:

```bash
rm -rf data
```

If you used the offline endpoint below, stop that process too:

```bash
kill "$FAKE_LLM_PID"
```

## Offline path: use the fake LLM

The fake endpoint returns predefined Smalltalk patches. It exercises the
same request, verification, and swap path without model inference or an API
key. From the repository root, use this setup instead of the real endpoint
configuration in section 0:

```bash
export LIVE_LLM_URL=http://host.docker.internal:8899/v1/chat/completions
export LIVE_LLM_MODEL=gpt-4o-mini
export LIVE_LLM_API_KEY=
python3 test/fakellm.py > /tmp/live-smalltalk-fakellm.log 2>&1 &
FAKE_LLM_PID=$!
```

Keep this shell open so you can use `FAKE_LLM_PID` for cleanup. The server
listens on port 8899 and normally produces no logs. You can check it with:

```bash
curl -s http://localhost:8899 -o /dev/null -w '%{http_code}\n'
# 501: the server is listening but only handles POST requests
```

Continue with [the base-image build and boot in section 0](#0-configure-the-llm-and-boot),
including the connection check, then follow the main tour in sections 1–4.
Sections 5–6 are optional; section 7 handles cleanup. If the containers are
already running, apply these endpoint variables with:

```bash
docker compose up -d --force-recreate supervisor
```

The automatic incident in section 3 includes `dispense`, which selects the
predefined correct method. The explicit request in section 5 selects it too.
For a deterministic rejection demo, run this **after breaking the method in
section 2 and before enabling automatic repair in section 3**:

```bash
./lk heal --problem "anything BOGUS-marker" --target VendingMachine
# "ok": false, "stage": "shadow-verify-failed"
./lk tests
# still failing: the rejected proposal did not repair the live method
```

`BOGUS` selects a patch that returns `'SABOTAGED'`; the suite rejects it in
the shadow VM. This trigger belongs to the fake server and has no special
meaning for a real model.

To return to the real endpoint in `.env`, clear the offline overrides and
recreate the supervisor:

```bash
unset LIVE_LLM_URL LIVE_LLM_MODEL LIVE_LLM_API_KEY
docker compose up -d --force-recreate supervisor
```

Then rerun the connection check in section 0.
