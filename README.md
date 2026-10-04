# live-smalltalk

An experiment in **using a language for the machine it gives you, not the
developer experience it gives you.** Smalltalk's killer feature isn't its
syntax (a modern reader finds it quaint) — it's that the image is a *live,
mutable object world*. You can compile a method into a running class and every
sender sees it instantly. No rebuild, no redeploy, no restart.

If an LLM is writing and testing all the code anyway, the DX cost of Smalltalk
stops mattering, and that live-image property becomes the whole point: **the
production image becomes a REPL the LLM codes into, validates in a clone, and
bakes when it's green.**

This repo is a working proof of that loop, on real Pharo 12.

**New here? Start with [`GUIDE.md`](GUIDE.md)** — a copy-pasteable lifecycle
tour: boot, hot-swap, break prod, self-heal, ship a feature, tear down.

## The loop

```
      want a feature / something broke
                 |
                 v
     [ lk eval / lk heal ]   -- host CLI
                 |
                 v
   +-------------------------------+        file queue, /data/queue
   |  LIVE prod VM (one process)   |<------ job-*.in  {type, code}
   |  QueueServer REPL loop        |------> job-*.out  {result}
   |  compiles INTO itself         |
   +-------------------------------+
        |            |
        |            |  candidate/repro staged to /data/workspace
        |            v
        |   +--------------------------+   shadow_run.sh: SEPARATE VM
        |   | shadow clone (base image) |<-- replays journal, applies
        |   | run repro + test suite    |    candidate, verdict to out.json
        |   +--------------------------+
        |            | green only
        |            v
        |   hot-swap into LIVE image  <-- LiveKernel>>eval: also appends to journal
        |            |
        v            v
   [ lk promote ]  -> tests green -> marker++ -> this is now "production"
```

The image is the **only** thing that mutates itself. The LLM (supervisor) only
ever *proposes*; the image validates every patch in a throwaway VM and only
swaps it if that VM says green. A malicious or hallucinating LLM cannot break
prod — the most it can do is waste a shadow-VM run. See `Adversarial` below.

## State model (no snapshots — every boot is reproducible)

The image deliberately does **not** save itself. State lives in `/data`:

| file | meaning |
|------|---------|
| `journal.json` | ordered array of every definition-shaped `lk eval` source |
| `marker.json` | `{n}` — journal entries `1..n` are *baked into production* |
| `config.json` | `llmUrl`, `llmModel`, `agentMode` |
| `healed.json` | self-heal history |
| `tests.json` | registered test class names |

Boot = pristine `base.image` + kernel install (from `kernel/methods.json`) +
**replay `journal[1..marker]`** + start the queue REPL.

This gives you the two-tier model you described without image diffs:

- **hot-swap** = `lk eval`. Compiles into the live VM *now*. Also appended to
  the journal (only if it compiled clean — see Landmines), so a restart can
  reproduce it — *but only if it gets promoted*.
- **tentative** = journalled but beyond `marker`. Survives normal running; a
  restart discards it. This is your "bakes in a little" window: try a change
  live, and if it's wrong, `docker restart` and it's gone.
- **promote** = `lk promote`. Runs the suite; **refuses if red**; else
  `marker = journalSize`. Now the change survives restart = it's production.
- **rollback** = `lk rollback <n>`. Next boot replays only `journal[1..n]`.

Verified end-to-end (see Evidence): promote survives `docker compose restart`;
an unpromoted tentative edit **vanishes** on restart while the promoted one
persists. That is exactly the "clone, test, bake, roll back" flow you sketched —
implemented as append-only journal + a marker, which is simpler and more
auditable than cloning whole VM images.

## Running it

```bash
docker compose up -d                      # prod VM + supervisor
./lk status                               # alive? journal/marker sizes
./lk tests                                # run the registered suites
./lk eval "Object subclass: #Greet ..."  # define a class, live, no restart
./lk promote                              # bake it if green
./lk candidate fix.st --repro r.st        # validate in a clone, DON'T touch live
./lk heal --problem "..." --repro r.st --target VendingMachine
./lk rollback 2                           # next boot replays journal[1..2]
./lk reset --yes                          # wipe /data
```

Self-healing needs an OpenAI-compatible endpoint. Point the supervisor at one:

```bash
export LIVE_LLM_URL=http://host.docker.internal:8899/v1/chat/completions
export LIVE_LLM_MODEL=gpt-4o-mini
docker compose up -d
# no real LLM? use the canned one that ships here (keyed off prompt text):
python3 test/fakellm.py &      # listens on 8899
```

## Self-healing, concretely

1. Image (or `lk heal`) writes `/data/heal/req-N.json`
   `{id, problem, repro, target, journalSize}`.
2. `agent/supervisor.py` sees it, calls the LLM, writes
   `/data/heal/resp-N.json` `{id, source}`.
3. Image **does not trust it.** It runs `Supervisor>>testCandidate:repro:` which
   spawns a **separate VM** over `base.image`, replays the journal, applies the
   candidate, evaluates the repro, runs the suite, writes a JSON verdict.
4. Only if that verdict is green does the image hot-swap the source in.

The LLM has no path to the live image that bypasses step 3.

## Evidence (all run against this repo, live containers)

| check | result |
|-------|--------|
| `lk eval` compiles a new class into the live VM; used by name in the *next* job | `'20pct off SPRING'`, no restart |
| `lk promote` refuses when a test fails | `ok=false, why: tests failing` |
| promoted change survives `docker compose restart` | persists |
| unpromoted tentative change lost on restart, promoted one kept | `'30pct'->'20pct'` |
| shadow: good additive patch | `ok=true`, **live untouched** |
| shadow: bad patch that breaks a test | `ok=false`, live untouched |
| shadow: repro asserts wrong value | `ok=false` (repro gate) |
| adversarial supervisor always returns a sabotage patch | `ok=false, stage=shadow-verify-failed` |
| sabotage that doesn't even compile | `ok=false` — caught by candidate-apply gate |
| self-heal: break `dispense` → LLM fix → swap | `ok=true, swapped=#dispense`, live green |
| `lk eval` defines a new `TestCase` subclass | auto-joins the suite (no manual register); breaking it makes `lk promote` refuse |

## Landmines found (Pharo 12 headless — this is the expensive part)

These cost the bulk of the iteration time; anyone repeating this should know:

- **`Object subclass:` inside one `eval` can't be referenced by literal name in
  the *same* eval** — it's undeclared at *compile* time. Cross-`eval` it's
  fine (the class is installed). Consequence: the LLM must define-then-use in
  **separate** messages, or reference via `Smalltalk globals at: #Foo`. The
  kernel's own installer therefore creates all classes in one pass, compiles
  methods in a second pass.
- **`displayNl`/`printNl`/`puts` do not exist on `String`** in Pharo 12. Use
  `Transcript show: ...; cr; flush`. Silent-wrong output burned a lot of time.
- **`loadst <file>` hangs the VM open on success** (reopens the image). The
  only reliable headless entry is `pharo --headless IMAGE eval "<one expr>"`.
  The whole kernel is therefore loaded via a single generated `eval`.
- **Keep the `.sources` file** next to `pharo.image` or snapshot/save aborts.
- **String quoting is a trap**: `compile:'sel..."..."...'` needs doubled single
  quotes, and an odd quote silently truncates the string (`SizeMismatch`). The
  kernel sidesteps hand-escaping entirely: Smalltalk sources live as **JSON
  strings** in `kernel/methods.json` (generated by `gen_methods.py`) and the
  loader `compile:`s them. `''''` never appears in source by hand.
- **Block-wrapped eval**: `LiveKernel>>evalQuiet:` wraps source as
  `'[' , src , '] value'` so a multi-statement job is one expression and a
  runtime error is caught, not raised.
- **Journal only clean compiles.** Initially `eval:` journalled even errored
  sources, and a promote baked garbage. Now a source is journalled only if its
  result isn't `ERROR:`/`EXCEPTION:`.
- **Don't substring-match the verdict.** Early shadow parsing looked for
  `"ok":true` in the output file — the nested `tests.ok` also matched, so a
  failed candidate read as green. Verdicts are now parsed as JSON.
- `FileReference entries`/`directoryEntries` differ; use `entries` + `isFile`.
- **New `TestCase` subclasses must register themselves.** `lk tests` runs the
  explicit registry in `tests.json`, not a global sweep — a naive
  `endsWith: 'Tests'` sweep drags in Pharo's own baked-in `Manifest*Tests` /
  `StDebugContextForTests` classes and the suite goes red on boot. So
  `LiveKernel>>eval:` snapshots `Smalltalk globals keys` before compiling and
  registers any newly-installed `*Tests` class *after* — but only on the live
  path. The shadow VM and boot replay share `/data` with the live kernel, so
  auto-registering there would let a *rejected candidate's* test class leak
  into the live registry and get promoted. Registration is a live-side effect
  gated on a clean compile, exactly like the journal append.

## Assessment — does the idea hold up?

**The strong version works.** Hot-swapping code into a live, tested image,
gated by a clone, baked by an append-only journal, is real, and it's a
genuinely different deployment model: *change is the default, restart is the
exception.* For an LLM operator that's a good fit — the agent gets an
interactive world to edit and a hard "green or it doesn't ship" gate that
doesn't depend on trusting the agent.

**Where it earns its keep**
- Instant iteration for long-running stateful services (game servers, sims,
  trading bots, this vending-machine style state) where a restart is costly.
- A safe shape for autonomous patching: the verifier lives in the language
  runtime (SUnit + a second VM), not in the LLM's good behaviour.

**Honest caveats — the things the demo can't paper over**
- **State migration is the unsolved hard part.** Hot-swapping a *method* is
  easy; hot-swapping a *class's ivar layout* when live instances already
  exist, or retiring behaviour that existing objects depend on, needs explicit
  migration hooks. SUnit passes don't catch "the 40 objects already in the heap
  are now the wrong shape." A production system needs before/after hooks on
  class redefinition (Metacello/`become:` territory).
- **Green suite ≠ correct.** Everything here is gated on tests, so the whole
  safety story is only as strong as the test coverage — an LLM that writes both
  the feature and its tests can satisfy a weak gate. The shadow VM is a
  *execution* guarantee, not a *specification* guarantee. Independent/adversarial
  test generation is the thing to add.
- **Journal replay is not real persistence.** In-memory hot state beyond the
  journal is lost on restart; anything you must keep belongs in real storage,
  not the image. The journal records *definitions*, not data.
- **Pharo is genuinely rough headless.** The GUI-less image story is second-class
  (every landmine above is headless-specific). It works, but you're patching
  around a system that assumes a dev environment.

**Net:** as a *deployment substrate for agent-maintained, long-lived,
stateful services*, this is the most compelling use of Smalltalk's live image
I've seen, and the hot-swap-verify-bake loop is sound and demonstrated. Treat
it as a *research prototype*: the loop is proven; the production gaps are state
migration and test independence, both real but bounded. Next steps worth doing:
class-redefinition hooks for instance migration, a second-image canary that
runs traffic against the candidate before promote, and making the test gate
non-self-authored.

## Layout

```
GUIDE.md                lifecycle walkthrough (start here)
kernel/gen_methods.py   generates methods.json + install/boot .st (edit this)
kernel/methods.json     the kernel's Smalltalk sources (generated)
kernel/boot_eval.st     the single eval that boots the container
scripts/serve.sh        container CMD: fresh image + boot + queue REPL
scripts/shadow_run.sh   runs a generated driver in a separate VM
agent/supervisor.py     the LLM side (proposes; never applies)
test/fakellm.py         canned OpenAI-compatible LLM for offline testing
lk                      host CLI: the file-queue client
Dockerfile.base         Pharo 12 VM+image (cached layer)
Dockerfile              live-smalltalk (kernel + scripts)
docker-compose.yml      prod + supervisor, /data bind-mounted
```
