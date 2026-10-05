#!/usr/bin/env python3
"""Supervisor: the LLM half of self-healing.

Watches /data/heal/req-N.json written by the image (AgentBridge), asks an
OpenAI-compatible chat endpoint for a fix, writes /data/heal/resp-N.json.
The IMAGE decides whether to swap: a returned patch only ever lands if the
image's own shadow-VM (base + journal + candidate + repro + suite) says OK.
Automatic incidents receive an explicit investigation decision before verification.

Env:
  HEAL_DIR          default ./data/heal
  LIVE_LLM_URL      default http://host.docker.internal:8899/v1/chat/completions
  LIVE_LLM_MODEL    default gpt-4o-mini
  LIVE_LLM_API_KEY  optional
"""
import json, os, re, sys, time, urllib.request

HEAL_DIR = os.environ.get("HEAL_DIR", "data/heal")
LLM_URL = os.environ.get("LIVE_LLM_URL", "http://host.docker.internal:8899/v1/chat/completions")
LLM_MODEL = os.environ.get("LIVE_LLM_MODEL", "gpt-4o-mini")
LLM_KEY = os.environ.get("LIVE_LLM_API_KEY", "")

SYSTEM = """You are a self-healing agent for a live Pharo Smalltalk image.
You get a failing repro and must return the SMALLEST fix as Smalltalk source
that, when evaluated in the image, repairs the behavior.

Rules:
- Reply with ONE fenced code block containing only source to evaluate.
- To fix a method, emit: ClassName compile: 'selector
-	<fixed body>'.
- To add a class, emit: Object subclass: #Name instanceVariableNames: '' ...
- Never emit markdown outside the code block. Never call APIs or test yourself.
"""


INVESTIGATE_SYSTEM = """You investigate failures in a live Pharo Smalltalk application.
Use the captured stack, source, receiver state, tests, and application contract.
Decide whether the failure is expected, lacks sufficient evidence, or needs repair.
Reply with ONE JSON object, optionally inside a json fence:
{"decision": "expected" | "insufficient-evidence" | "repair", "reason": "...", "source": "..."}
For repair, source must be the SMALLEST Smalltalk statements that compile the fix
into the target class: one TargetClass compile: statement for the captured selector.
Single quotes inside compile: strings must be doubled.
Do not change tests, repair policy, kernel classes, or instance variable layouts.
Do not access files, APIs, globals holding live application state, or run tests.
Use the contract and existing tests as the specification. A raised exception by
itself does not imply a bug. For other decisions omit source.
"""


def chat(req, system):
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": json.dumps(req)}],
        "temperature": 0.2,
    }).encode()
    request = urllib.request.Request(LLM_URL, data=body, headers={
        "Content-Type": "application/json",
        **({"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {})})
    with urllib.request.urlopen(request, timeout=120) as resp:
        data = json.loads(resp.read())
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Endpoint returned no text")
    return content.strip()


def extract_source(text):
    fences = re.findall(r"```(?:st|smalltalk)?\s*\n(.*?)```", text, re.S)
    return ("\n".join(f.strip() for f in fences)).strip() if fences else text.strip()


def call_llm(req):
    return extract_source(chat(req, SYSTEM))


def investigate(req):
    text = chat(req, INVESTIGATE_SYSTEM)
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n?```", text, re.S)
    answer = json.loads(fence.group(1) if fence else text)
    if not isinstance(answer, dict):
        raise ValueError("Investigation must return an object")
    decision = answer.get("decision")
    if decision not in ("expected", "insufficient-evidence", "repair"):
        raise ValueError("Invalid investigation decision")
    reason = answer.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Investigation must explain its decision")
    result = {"decision": decision, "reason": reason}
    if decision == "repair":
        source = answer.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Repair decision requires source")
        result["source"] = extract_source(source)
    return result


def already_handled(f):
    base = os.path.basename(f)
    resp = os.path.join(HEAL_DIR, base.replace("req-", "resp-"))
    return os.path.exists(resp)


def process(f):
    try:
        with open(f) as fh:
            req = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"skipping {f}: {e}", file=sys.stderr)
        return
    rid = req.get("id")
    if rid is None:
        return
    resp_path = os.path.join(HEAL_DIR, f"resp-{rid}.json")
    if os.path.exists(resp_path):
        return
    print(f"[supervisor] healing req {rid}: {req.get('problem','')[:80]}")
    payload = None
    for attempt in range(2):
        try:
            payload = investigate(req) if req.get("kind") == "incident" else {"source": call_llm(req)}
            break
        except Exception as e:
            print(f"[supervisor] LLM failed for {rid}: {e}", file=sys.stderr)
            if attempt == 0:
                time.sleep(2)
            else:
                payload = {"error": str(e)}
    payload.update(id=rid, supervisor="llm", model=LLM_MODEL)
    tmp = resp_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, resp_path)
    print(f"[supervisor] answered req {rid} ({payload.get('decision', 'proposal')})")


def main():
    os.makedirs(HEAL_DIR, exist_ok=True)
    print(f"[supervisor] watching {HEAL_DIR}; LLM={LLM_MODEL} @ {LLM_URL}")
    while True:
        try:
            for name in sorted(os.listdir(HEAL_DIR)):
                if name.startswith("req-") and name.endswith(".json"):
                    process(os.path.join(HEAL_DIR, name))
        except Exception as e:
            print(f"[supervisor] loop error: {e}", file=sys.stderr)
        time.sleep(1)


if __name__ == "__main__":
    main()
