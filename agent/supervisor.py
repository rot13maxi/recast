#!/usr/bin/env python3
"""Supervisor: the LLM half of self-healing.

Watches /data/heal/req-N.json written by the image (AgentBridge), asks an
OpenAI-compatible chat endpoint for a fix, writes /data/heal/resp-N.json.
The IMAGE decides whether to swap: a returned patch only ever lands if the
image's own shadow-VM (base + journal + candidate + repro + suite) says OK.
This process can therefore be adversarial and nothing bad happens.

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


def call_llm(req):
    prompt = (f"Target: {req.get('target','')}\n"
              f"Problem: {req.get('problem','')}\n"
              f"Repro (Smalltalk that currently misbehaves):\n"
              f"```\n{req.get('repro','')}\n```\n"
              f"Journal size: {req.get('journalSize')}")
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt}],
        "temperature": 0.2,
    }).encode()
    r = urllib.request.Request(LLM_URL, data=body, headers={
        "Content-Type": "application/json",
        **({"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {})})
    with urllib.request.urlopen(r, timeout=120) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"]["content"]
    fences = re.findall(r"```(?:st|smalltalk)?\s*\n(.*?)```", text, re.S)
    source = ("\n".join(f.strip() for f in fences)).strip()
    if not source:
        source = text.strip()
    return source


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
    try:
        source = call_llm(req)
    except Exception as e:
        print(f"[supervisor] LLM failed for {rid}: {e}", file=sys.stderr)
        time.sleep(2)
        try:
            source = call_llm(req)
        except Exception as e2:
            with open(resp_path, "w") as fh:
                json.dump({"id": rid, "error": str(e2)}, fh)
            return
    tmp = resp_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"id": rid, "source": source,
                   "supervisor": "llm", "model": LLM_MODEL}, fh)
    os.rename(tmp, resp_path)
    print(f"[supervisor] answered req {rid} ({len(source)} chars)")


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
