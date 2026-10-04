#!/usr/bin/env python3
"""Canned OpenAI-compatible chat server for exercising the self-healing loop
without a real LLM. Returns fixes keyed off the prompt text:

  prompt contains 'dispense'   -> correct VendingMachine>>dispense
  prompt contains 'BOGUS'      -> a WRONG fix (must be caught by shadow verify)
  otherwise                    -> 'nil'

Every reply is fenced so supervisor.py's fence-extraction is exercised too.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

FIX = {"model": "gpt-4o-mini", "choices": [{"message": {"content":
    "```smalltalk\n"
    "VendingMachine compile: 'dispense\n"
    "\tpaid >= price ifFalse: [ self error: ''insufficient credit'' ].\n"
    "\tpaid := paid - price.\n"
    "\t^ ''product'''\n"
    "```"}}]}

BOGUS = {"model": "gpt-4o-mini", "choices": [{"message": {"content":
    "```smalltalk\nVendingMachine compile: 'dispense\n\t^ ''SABOTAGED'''\n```"}}]}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        msgs = body.get("messages", [])
        prompt = " ".join(m.get("content", "") for m in msgs)
        if "BOGUS" in prompt:
            resp = BOGUS
        elif "dispense" in prompt:
            resp = FIX
        else:
            resp = NILLIKE
        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8899), H).serve_forever()
