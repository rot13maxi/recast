# Getting started

A guided tour of the whole lifecycle: boot → see it work → break prod →
watch the self-heal loop fix it → ship a new feature → promote, persist,
tear down. Every command below was run verbatim against this repo.

**Prereqs:** Docker with the compose plugin, Python 3 on the host. From this
directory (`live-smalltalk/`).

> The two Pharo landmines this tour will bite you on if you freelance:
> a class defined in an `lk eval` can't be referenced by name *in the same*
> eval (undeclared at compile time) — define in one eval, use in the next.
> And only sources that compile clean are journalled, so `promote` never
> bakes a syntax error.

## 0. Boot

```bash
docker compose up -d --build          # prod VM + supervisor
python3 test/fakellm.py &           # canned "LLM" on :8899 for the self-heal demo
sleep 12                            # VM boots: base image + journal replay + REPL
./lk status
```

`fakellm.py` prints nothing when healthy (it logs nothing); confirm with
`curl -s localhost:8899 -o /dev/null -w '%{http_code}\n'` — expect `501`
(it's POST-only; rejecting a GET proves it's listening). `000` = dead.
`status` is your image liveness check — the
image heartbeats to `/data/queue/` and the CLI measures the file mtime:

```json
{ "alive": true, "journalSize": 0, "marker": 0, "heartbeatAgeSec": 0.0,
  "agentMode": "live", "testClasses": ["VendingMachineTests"], "...": "..." }
```

The app inside is one demo class, `VendingMachine`: insert coins until you
have 50, `dispense` returns `'product'`.

```bash
./lk eval "| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense"
```

prints `'product'`. That command was **evaluated inside the running
production VM** — the output is the VM's `printString` of the result.

Green baseline suite:

```bash
./lk tests
# "run": 2, "ok": true  — VendingMachineTests
```

## 1. See it work: hot-swap a class, no restart

Smalltalk's trick — compile a brand-new class into the running VM and use
it seconds later. Two evals, because define-and-use can't share one eval:

```bash
./lk eval "Smalltalk globals at: #Greeting ifAbsent: [
  Object subclass: #Greeting instanceVariableNames: '' classVariableNames: ''
    poolDictionaries: '' package: 'Demo' ]. nil"

./lk eval "Greeting compile: 'greet: name
  ^ ''hello, '', name , ''!'''. nil"

./lk eval "Greeting new greet: 'world'"        # -> 'hello, world!'
```

Quote-logic for `compile:` inside a shell arg: the method source is itself
a Smalltalk string, so *every* quote inside it doubles. The literal `'!'`
becomes `''!''` **inside** the outer string, which then needs its own
closing quote → three quotes in a row (`!'''`). An odd number of quotes
silently eats the rest as a comment or errors `Unmatched '` — that's the
same trap the README's landmine list documents.

No build, no deploy, no restart — `Greeting` exists in the live image right
now. It's journalled so a restart *could* reproduce it, but it's **tentative**
until promoted (we'll see what that means in §4).

Heads-up: `./lk logs` is ~90% `DeprecationPerformedNotification` noise
triggered by the `Object subclass:` idiom itself. It's harmless if you see
`LK-BOOT: replayed N entries, 0 failures`; only growing LK- lines matter.

## 2. See it fail: break prod

Sabotage the vending machine — this compiles fine, so it hot-swaps in, and
now `dispense` always raises:

```bash
./lk eval "VendingMachine compile: 'dispense
  self error: ''the machine is on fire'''. nil"

./lk eval "| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense"
# -> ERROR: Error: the machine is on fire
#    (raised errors come back "ERROR: <ErrorClass>: <description>", NOT
#     "EXCEPTION:" — evalQuiet returns that shape; don't grep for EXCEPTION)
```

The gate notices:

```bash
./lk tests        # "ok": false — testDispenseAfterCredit fails
./lk promote      # refuses: "ok": false, "why": "refusing to promote: tests failing"
```

**Production can't bake a regression** — promote is gated on the suite, and
the suite runs *in the image itself*. You could also `docker compose restart
prod` right now and the sabotage would vanish (un-promoted journal entries
die on restart). But the point of this demo is the other fix path...

## 3. Self-heal: the LLM proposes, the image disposes

The supervisor container is the LLM's hands; the image never trusts them.
`lk heal` has the image write a request (`/data/heal/req-N.json`) with the
problem, a repro script, and the broken target class; the supervisor polls,
calls the LLM, and writes back a proposed patch. Then **the image** applies
the patch in a throwaway shadow VM (fresh VM, journal replayed, candidate
applied, repro + full suite run) and only hot-swaps it into prod if the
shadow verdict is green.

The canned `fakellm.py` plays the LLM: if the prompt mentions `dispense` it
returns the correct fixed method, if it mentions `BOGUS` it returns a
sabotage patch (so you can watch the gate reject it).
If `lk heal` ever answers `"stage": "supervisor-timeout"` (~2 min), the
supervisor container is down (`docker compose ps`). `"stage":
"bad-supervisor-response"` or an `error` in `data/heal/resp-N.json` means
fakellm.py died or the problem text hit an unhandled branch (see its
docstring). Always `curl`-check 8899 before §3.

First, watch the gate work — ask for a fix whose prompt trips `BOGUS`:

```bash
./lk heal --problem "anything BOGUS-marker" \
          --target VendingMachine
# "ok": false, "stage": "shadow-verify-failed"
# prod untouched, sabotage caught in the shadow VM
```

Now the real heal. Write a repro (any expression that errors on the broken
code and returns something non-`false`/`nil` when fixed):

```bash
cat > /tmp/repro.st <<'EOF'
| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense = 'product'
EOF

./lk heal --problem "dispense always raises 'the machine is on fire'" \
          --repro /tmp/repro.st --target VendingMachine
# "ok": true, "swapped": "#dispense"  (takes a few s: poll + shadow VM run)

./lk eval "| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense"
# -> 'product'   ← fixed, live, no restart — the image compiled the LLM's
#                   patch into itself after the shadow VM said green
```

Audit trail:

```bash
./lk tests        # green again
./lk healed       # SUCCESSFUL heals only: id, target, source, swapResult
                  # (rejection like the BOGUS one is NOT recorded here — it lives
                  #  only in data/heal/req-1.json (+ its resp-1.json); ls that dir
ls data/heal/     # req-N.json / resp-N.json — the raw proposals
```

Note the fix is **tentative** right now. If the "LLM" had been subtly wrong
and the suite too weak to catch it, `docker compose restart prod` would roll
it back — the shadow-verified-but-unpromoted state is your bake window.

## 4. Ship the feature: promote, then prove persistence

Promote bakes *all* journalled definitions (the new `Greeting`, the healed
`dispense`) into production by advancing the marker:

```bash
./lk promote      # "ok": true, "marker": N  (refuses first if anything is red)
docker compose restart prod
sleep 12
./lk status       # journalSize: N, marker: N — boot replays exactly these
./lk eval "Greeting new greet: 'again'"     # -> 'hello, again!'
./lk tests        # still green
```

The restarted VM rebuilt itself from `base.image` + the promoted journal —
deterministic boot, no saved-state snapshots, and `rollback <n>` + restart
rewinds to any older bake point.

Try the tentative window yourself:

```bash
./lk eval "Greeting compile: 'greet: name
  ^ ''bonjour, '', name'. nil"
./lk eval "Greeting new greet: 'maintenant'"   # -> 'bonjour, maintenant'
docker compose restart prod                     # tentative edit dies here
sleep 12
./lk eval "Greeting new greet: 'again'"         # -> 'hello, again!'
```

The promoted `greet:` survives; the tentative one evaporates with the VM.

## 5. Optional detours

Run these **last** — `rollback 0` + restart and `reset --yes` are
destructive to everything above (rollback 0 makes `Greeting` undeclared
again in the live image; `reset --yes` also wipes the `data/heal` audit
trail). They are the rewind-to-zero path, not part of the spine.

```bash
./lk logs                        # prod VM logs (see the §1 noise warning)
# shadow-validate a patch WITHOUT touching the live image:
cat > /tmp/fix.st <<'EOF'
VendingMachine compile: 'dispense
	paid >= price ifFalse: [ self error: ''insufficient credit'' ].
	paid := paid - price.
	^ ''product'''
EOF
cat > /tmp/r.st <<'EOF'
| vm | vm := VendingMachine make. vm insertCoin: 60. vm dispense = 'product'
EOF
./lk candidate /tmp/fix.st --repro /tmp/r.st   # "ok": true; live image untouched
./lk rollback 0 && docker compose restart prod # rewind: Greeting gone again
sleep 12
./lk eval "Greeting new greet: 'gone'"   # ERROR: ...Undeclared variable->Greeting
./lk reset --yes                 # wipe /data entirely, reboot pristine
```

## 6. Stop and clean up

```bash
docker compose down              # stop both containers
pkill -f fakellm.py                            # stop the fake LLM (job-control
                                                #  %1 only works in the shell that
                                                #  started it — pkill is the safe net)
rm -rf data                      # clear journal/heal state (or keep it)
```

`data/` is the only mutable state; containers are disposable. `docker
compose up -d` later resumes from `data/` exactly as it was.

## Using a real LLM instead of the canned one

```bash
export LIVE_LLM_URL=https://api.openai.com/v1/chat/completions   # or your proxy
export LIVE_LLM_MODEL=gpt-4o-mini
export LIVE_LLM_API_KEY=***
docker compose up -d --force-recreate supervisor
```

Back to canned mode: `unset LIVE_LLM_URL LIVE_LLM_MODEL LIVE_LLM_API_KEY`
then `docker compose up -d --force-recreate supervisor`.

The contract the model must satisfy: reply with a fenced ```smalltalk block
whose contents are Smalltalk statements that compile into place (method
`compile:` sources, class definitions...). Anything it hallucinates dies in
the shadow VM, so be as adversarial with your endpoint as you like — try
`fakellm.py`'s sabotage mode if you want to see the trust model hold.

