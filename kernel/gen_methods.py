#!/usr/bin/env python3
"""Generate kernel/methods.json -- the kernel's own Smalltalk sources.

Method sources are plain Python strings -> JSON, so Smalltalk quote-escapes
never get double-escaped by hand. kernel/install.st loads this file and
compiles every entry into the live image. Regenerate: python3 gen_methods.py
"""
import json, os

def cls(name, ivars="", package="LiveKernel", methods=()):
    return {"name": name, "ivars": ivars, "package": package,
            "methods": list(methods)}

PARAMS = {
    'ensureDir:': ['aPath'], 'pathFor:': ['aString'],
    'readJson:default:': ['fileName', 'defaultValue'],
    'writeJson:value:': ['fileName', 'anObject'],
    'writeMarker:': ['n'], 'journalAppend:': ['sourceString'],
    'replayUpTo:': ['n'],
    'reproPassed:': ['aString'],
    'evalResultFailed:': ['aString'], 'isDefinition:': ['sourceString'],
    'eval:': ['sourceString'], 'evalQuiet:': ['sourceString'],
    'truncate:to:': ['aString', 'n'],
    'configAt:put:': ['key', 'value'],
    'configAt:default:': ['key', 'defaultValue'],
    'noteHealed:': ['entry'], 'addTestClassNamed:': ['aString'],
    'rollbackTo:': ['n'], 'shadowRun:': ['spec'],
    'handleJob:': ['jsonText'], 'handleHeal:': ['job'],
    'testCandidate:repro:': ['candidateSource', 'reproSource'],
    'writeTemp:contents:extension:': ['prefix', 'aString', 'ext'],
    'driverForCandidate:repro:out:': ['candidatePath', 'reproPath', 'outPath'],
    'verify:description:': ['aBoolean', 'desc'],
    'price:': ['anAmount'], 'insertCoin:': ['anAmount'],
}


def pattern(sel, params=None):
    if ':' not in sel:
        return sel
    params = params or PARAMS[sel]
    parts = [p for p in sel.split(':') if p != '']
    return ' '.join(f'{kw}: {p}' for kw, p in zip(parts, params))


def m(sel, source, class_side=False, params=None):
    full = pattern(sel, params) + '\n' + source
    return {"sel": sel, "classSide": class_side, "source": full}

CLASSES = [

cls("LiveKernel", methods=[
 m("dataDir", r"""^ '/data'""", True),
 m("ensureDir:", r"""LibC system: 'mkdir -p ', aPath asString, ' >/dev/null'""", True),
 m("pathFor:", r"""^ self dataDir, '/', aString""", True),
 m("readJson:default:", r"""| ref |
	ref := (self pathFor: fileName) asFileReference.
	ref exists ifFalse: [ ^ defaultValue ].
	^ [ STONJSON fromString: ref contents ]
		on: Error do: [ :e | defaultValue ]""", True),
 m("writeJson:value:", r"""| final tmp |
	self ensureDir: self dataDir.
	final := (self pathFor: fileName) asFileReference.
	tmp := (self pathFor: fileName, '.tmp') asFileReference.
	tmp writeStreamDo: [ :s | s nextPutAll: (STONJSON toString: anObject) ].
	final exists ifTrue: [ final ensureDelete ].
	tmp moveTo: final""", True),
 m("marker", r"""^ (self readJson: 'marker.json'
		default: (Dictionary new at: 'n' put: 0; yourself))
		at: 'n' ifAbsent: [ 0 ]""", True),
 m("writeMarker:", r"""self writeJson: 'marker.json'
		value: (Dictionary new at: 'n' put: n asInteger; yourself)""", True),
 m("journal", r"""^ (self readJson: 'journal.json' default: #()) asArray""", True),
 m("journalAppend:", r"""| j |
	j := self journal asOrderedCollection.
	j add: sourceString asString.
	self writeJson: 'journal.json' value: j asArray""", True),
 m("replayUpTo:", r"""| failures j limit |
	failures := OrderedCollection new.
	j := self journal.
	limit := n min: j size.
	1 to: limit do: [ :i | | r |
		r := self evalQuiet: (j at: i).
		((r beginsWith: 'ERROR:') or: [ r beginsWith: 'EXCEPTION:' ])
			ifTrue: [ failures add: (i -> r) ] ].
	^ failures""", True),
 m("isDefinition:", r"""| s |
	s := sourceString asString.
	^(s includesSubstring: 'subclass:')
		or: [ (s includesSubstring: 'compile:')
		or: [ (s includesSubstring: 'removeSelector:')
		or: [ (s includesSubstring: 'instanceVariableNames:') ] ] ]""", True),
 m("eval:", r"""| source before result clean |
	source := sourceString asString.
	before := Smalltalk globals keys asSet.
	result := self evalQuiet: source onError: [ :error |
        (self isDefinition: source) ifFalse: [
            LLMRepairService current ifNotNil: [ :service |
                [ service capture: error source: source ] on: Error do: [ :captureError |
                    Transcript show: 'LK-INCIDENT-ERROR: ', captureError asString; cr; flush ] ] ] ].
	((source beginsWith: '// nojournal') not
		and: [ self isDefinition: source ]) ifTrue: [
		clean := ((result beginsWith: 'ERROR:')
			or: [ (result beginsWith: 'EXCEPTION:')
			or: [ (result beginsWith: 'NOTIFICATION:') ] ]) not.
		clean ifTrue: [
			self journalAppend: source.
			(Smalltalk globals keys asSet difference: before) do: [ :sym |
				(sym asString endsWith: 'Tests')
					ifTrue: [ self addTestClassNamed: sym asString ] ] ] ].
	^ result""", True),
 m("evalQuiet:", r"""^ self evalQuiet: sourceString onError: [ :error | ]""", True),
 m("evalQuiet:onError:", r"""| result wrapped |
    wrapped := '[' , sourceString asString , '] value'.
    result := [ Smalltalk compiler evaluate: wrapped ]
        on: Error do: [ :error | handler value: error. ^ 'ERROR: ', error asString ].
    ^ result isNil ifTrue: [ 'nil' ] ifFalse: [
        [ result printString ] on: Error do: [ :error | 'a ', result class name asString ] ]""", True, params=['sourceString', 'handler']),
 m("truncate:to:", r"""| s |
	s := aString asString.
	^ s size <= n ifTrue: [ s ] ifFalse: [ s copyFrom: 1 to: n ]""", True),
 m("boot", r"""| failures |
	failures := self replayUpTo: self marker.
	Transcript show: ('LK-BOOT: replayed ', self marker asString, ' entries, ',
		failures size asString, ' failures'); cr; flush.
	^ 'boot ok'""", True),
 m("shadowRun:", r"""| n candidatePath reproPath outPath report tests reproResult candResult |
	n := spec at: 'n' ifAbsent: [ 0 ].
	candidatePath := spec at: 'candidate'.
	reproPath := spec at: 'repro' ifAbsent: [ nil ].
	outPath := spec at: 'out'.
	self replayUpTo: n.
	candResult := self evalQuiet: candidatePath asFileReference contents.
	reproResult := 'skipped'.
	reproPath ifNotNil: [ :p |
		(p asFileReference exists
			and: [ p asFileReference contents ~= 'nil' ])
				ifTrue: [ reproResult := self evalQuiet: p asFileReference contents ] ].
	tests := TestRunner runAll.
	report := Dictionary new
		at: 'tests' put: tests;
		at: 'repro' put: (self truncate: reproResult to: 500);
		yourself.
	report at: 'candidate' put: (self truncate: candResult to: 300).
	report at: 'ok' put: ((tests at: 'ok')
		and: [ (self reproPassed: reproResult)
		and: [ (self evalResultFailed: candResult) not ] ]).
	(outPath asFileReference) parent ensureCreateDirectory.
	outPath asFileReference writeStreamDo: [ :s |
		s nextPutAll: (STONJSON toString: report) ].
	^ report""", True),
 m("evalResultFailed:", r"""| r |
	r := aString asString.
	^ (r beginsWith: 'ERROR:')
		or: [ (r beginsWith: 'EXCEPTION:')
		or: [ (r beginsWith: 'NOTIFICATION:') ] ]""", True),
 m("reproPassed:", r"""| r |
	r := aString asString.
	(r = 'skipped' or: [ r = 'none' or: [ r = 'nil' ] ])
		ifTrue: [ ^ true ].
	(r beginsWith: 'EXCEPTION:') ifTrue: [ ^ false ].
	(r beginsWith: 'ERROR:') ifTrue: [ ^ false ].
	^ (r = 'false') not""", True),
 m("config", r"""^ self readJson: 'config.json' default: Dictionary new""", True),
 m("configAt:put:", r"""| c |
	c := self config.
	c at: key asString put: value.
	self writeJson: 'config.json' value: c.
	^ c""", True),
 m("configAt:default:", r"""^ self config at: key asString ifAbsent: [ defaultValue ]""", True),
 m("healed", r"""^ (self readJson: 'healed.json' default: #()) asArray""", True),
 m("noteHealed:", r"""| h |
	h := self healed asOrderedCollection.
	h add: entry.
	self writeJson: 'healed.json' value: h asArray""", True),
 m("addTestClassNamed:", r"""| names |
	names := self testClassNames asOrderedCollection.
	(names includes: aString asString) ifFalse: [
		names add: aString asString.
		self writeJson: 'tests.json' value: names asArray ]""", True),
 m("testClassNames", r"""^ (self readJson: 'tests.json'
		default: #('VendingMachineTests')) asArray""", True),
 m("promote", r"""| tests |
	tests := TestRunner runAll.
	(tests at: 'ok') ifFalse: [
		^ (Dictionary new
			at: 'ok' put: false;
			at: 'why' put: 'refusing to promote: tests failing';
			at: 'tests' put: tests;
			yourself) ].
	self writeMarker: self journal size.
	^ (Dictionary new
		at: 'ok' put: true;
		at: 'marker' put: self marker;
		at: 'journalSize' put: self journal size;
		at: 'tests' put: tests;
		yourself)""", True),
 m("rollbackTo:", r"""| safe |
	safe := n asInteger min: self journal size max: 0.
	self writeMarker: safe.
	^ (Dictionary new at: 'ok' put: true; at: 'marker' put: safe; yourself)""", True),
 m("status", r"""| hb age |
	hb := (self pathFor: 'queue/heartbeat.txt') asFileReference.
	age := hb exists
		ifTrue: [ [ (Time millisecondClockValue - hb contents asInteger) abs ]
			on: Error do: [ :e | -2 ] ]
		ifFalse: [ -1 ].
	^ (Dictionary new
		at: 'journalSize' put: self journal size;
		at: 'marker' put: self marker;
		at: 'testClasses' put: self testClassNames;
		at: 'healedCount' put: self healed size;
        at: 'automaticRepair' put: (self configAt: 'automaticRepair' default: false);
		at: 'agentMode' put: (self configAt: 'agentMode' default: 'live');
		at: 'llmModel' put: (self configAt: 'llmModel' default: '');
		at: 'heartbeatAgeSec' put: age;
		at: 'alive' put: (age >= 0 and: [ age < 30 ]);
		yourself)""", True),
]),

cls("TestRunner", methods=[
 m("runAll", r"""| passed failed descriptions |
	passed := 0. failed := 0. descriptions := OrderedCollection new.
	LiveKernel testClassNames do: [ :className | | cls |
		cls := Smalltalk globals at: className asSymbol ifAbsent: [ nil ].
		cls ifNotNil: [
			(cls selectors select: [ :s | s asString beginsWith: 'test' ])
				do: [ :selector | | outcome |
					outcome := [ cls new perform: selector. nil ]
						on: Error do: [ :e | e ].
					outcome isNil
						ifTrue: [ passed := passed + 1 ]
						ifFalse: [
							failed := failed + 1.
							descriptions add: (LiveKernel truncate:
								(className, '>>', selector asString, ': ', outcome asString)
								to: 400) ] ] ] ].
	^ Dictionary new
		at: 'run' put: passed + failed;
		at: 'passed' put: passed;
		at: 'failed' put: failed;
		at: 'ok' put: failed = 0;
		at: 'failures' put: descriptions asArray;
		yourself""", True),
]),

cls("QueueServer", methods=[
 m("queueDir", r"""^ LiveKernel dataDir, '/queue'""", True),
 m("ensureQueue", r"""LiveKernel ensureDir: self queueDir""", True),
 m("loop", r"""| running |
    LLMRepairService start.
	self ensureQueue.
	Transcript show: ('LK-READY: queue at ', self queueDir); cr; flush.
	running := true.
	[ running ] whileTrue: [
		| dir |
		dir := self queueDir asFileReference.
		(dir entries select: [ :e | (e basename asString endsWith: '.in')
				and: [ e isFile ] ])
			asArray
			do: [ :e | | name content reply outName |
				name := e basename asString.
				content := [ (dir / name) contents ]
					on: Error do: [ :x | '{}' ].
				reply := [ self handleJob: content ]
					on: Error do: [ :er | 'JOB-ERROR: ', er asString ].
				outName := name copyFrom: 1 to: name size - 3.
				(dir / (outName, '.out'))
					writeStreamDo: [ :s | s nextPutAll: reply asString ].
				[ (dir / name) ensureDelete ] on: Error do: [ :x | ].
				(reply = 'SHUTDOWN') ifTrue: [ running := false ] ].
        [ LLMRepairService current poll ] on: Error do: [ :error |
            Transcript show: 'LK-REPAIR-ERROR: ', error asString; cr; flush ].
		[ (dir / 'heartbeat.txt')
			writeStreamDo: [ :s | s nextPutAll: Time millisecondClockValue printString ] ]
			on: Error do: [ :x | ].
		(Delay forMilliseconds: 100) wait ].
	Transcript show: 'LK-BYE'; cr; flush.
	Smalltalk snapshot: false andQuit: true""", True),
 m("handleJob:", r"""| job kind |
	job := [ STONJSON fromString: jsonText ] on: Error do: [ :e | nil ].
	job ifNil: [ ^ LiveKernel truncate: 'ERROR: bad job JSON' to: 800 ].
	kind := job at: 'type' ifAbsent: [ 'eval' ].
	kind = 'eval' ifTrue: [
		^ LiveKernel eval: (job at: 'code' ifAbsent: [ 'nil' ]) ].
	kind = 'tests' ifTrue: [ ^ STONJSON toString: TestRunner runAll ].
	kind = 'status' ifTrue: [ ^ STONJSON toString: LiveKernel status ].
	kind = 'promote' ifTrue: [ ^ STONJSON toString: LiveKernel promote ].
	kind = 'rollback' ifTrue: [
		^ STONJSON toString: (LiveKernel rollbackTo:
			(job at: 'marker' ifAbsent: [ 0 ])) ].
	kind = 'test_candidate' ifTrue: [
		^ STONJSON toString: (Supervisor
			testCandidate: (job at: 'code' ifAbsent: [ 'nil' ])
			repro: (job at: 'repro' ifAbsent: [ nil ])) ].
	kind = 'config' ifTrue: [
		(job at: 'llmUrl' ifAbsent: [ nil ]) ifNotNil: [ :u |
			LiveKernel configAt: 'llmUrl' put: u ].
		(job at: 'llmModel' ifAbsent: [ nil ]) ifNotNil: [ :md |
			LiveKernel configAt: 'llmModel' put: md ].
        (job at: 'automaticRepair' ifAbsent: [ nil ]) ifNotNil: [ :enabled |
            LiveKernel configAt: 'automaticRepair' put: enabled ].
		(job at: 'agentMode' ifAbsent: [ nil ]) ifNotNil: [ :md |
			LiveKernel configAt: 'agentMode' put: md asString ].
		^ STONJSON toString: LiveKernel status ].
	kind = 'heal' ifTrue: [ ^ STONJSON toString: (AgentBridge handleHeal: job) ].
	kind = 'incidents' ifTrue: [ ^ STONJSON toString: LLMRepairService current incidents ].
	kind = 'healed' ifTrue: [ ^ STONJSON toString: LiveKernel healed ].
	kind = 'shutdown' ifTrue: [ ^ 'SHUTDOWN' ].
	^ LiveKernel truncate: 'ERROR: unknown job type: ', kind asString to: 800""", True),
]),

cls("AgentBridge", methods=[
 m("healDir", r"""^ LiveKernel dataDir, '/heal'""", True),
 m("ensureHealDir", r"""LiveKernel ensureDir: self healDir""", True),
 m("nextId", r"""| ref n |
	self ensureHealDir.
	ref := (self healDir, '/.seq') asFileReference.
	n := ref exists
		ifTrue: [ [ ref contents asInteger ] on: Error do: [ :e | 0 ] ]
		ifFalse: [ 0 ].
	n := n + 1.
	ref writeStreamDo: [ :s | s nextPutAll: n asString ].
	^ n""", True),
 m("handleHeal:", r"""| id request reqFile respFile answer source verdict swapped |
	id := self nextId.
	request := Dictionary new
		at: 'id' put: id;
		at: 'problem' put: (job at: 'problem' ifAbsent: [ '' ]);
		at: 'repro' put: (job at: 'repro' ifAbsent: [ '' ]);
		at: 'target' put: (job at: 'target' ifAbsent: [ '' ]);
		at: 'journalSize' put: LiveKernel journal size;
		yourself.
	reqFile := (self healDir, '/req-', id asString, '.json') asFileReference.
	reqFile writeStreamDo: [ :s | s nextPutAll: (STONJSON toString: request) ].
	respFile := (self healDir, '/resp-', id asString, '.json') asFileReference.
	1 to: 120 do: [ :tick |
		respFile exists ifFalse: [ (Delay forMilliseconds: 1000) wait ] ].
	respFile exists ifFalse: [
		^ Dictionary new
			at: 'ok' put: false;
			at: 'stage' put: 'supervisor-timeout';
			at: 'id' put: id; yourself ].
	answer := [ STONJSON fromString: respFile contents ]
		on: Error do: [ :e | nil ].
	source := answer isNil
		ifTrue: [ nil ]
		ifFalse: [ answer at: 'source' ifAbsent: [ nil ] ].
	source ifNil: [
		^ Dictionary new
			at: 'ok' put: false;
			at: 'stage' put: 'bad-supervisor-response';
			at: 'id' put: id; yourself ].
	verdict := Supervisor testCandidate: source
		repro: (job at: 'repro' ifAbsent: [ nil ]).
	(verdict at: 'ok' ifAbsent: [ false ]) ifFalse: [
		^ (Dictionary new
			at: 'ok' put: false;
			at: 'stage' put: 'shadow-verify-failed';
			at: 'id' put: id;
			at: 'source' put: (LiveKernel truncate: source to: 2000);
			at: 'shadow' put: (verdict at: 'detail' ifAbsent: [ '' ]);
			yourself) ].
	swapped := LiveKernel eval: source.
	(LiveKernel evalResultFailed: swapped) ifTrue: [
		^ (Dictionary new
			at: 'ok' put: false;
			at: 'stage' put: 'swap-failed';
			at: 'id' put: id;
			at: 'swapped' put: (LiveKernel truncate: swapped to: 300);
			at: 'source' put: (LiveKernel truncate: source to: 2000);
			yourself) ].
	LiveKernel noteHealed: (Dictionary new
		at: 'id' put: id;
		at: 'target' put: (job at: 'target' ifAbsent: [ '' ]);
		at: 'swapResult' put: (LiveKernel truncate: swapped to: 300);
		at: 'source' put: (LiveKernel truncate: source to: 2000);
		yourself).
	^ Dictionary new
		at: 'ok' put: true;
		at: 'id' put: id;
		at: 'swapped' put: (LiveKernel truncate: swapped to: 300);
		at: 'source' put: (LiveKernel truncate: source to: 2000);
		yourself""", True),
]),

cls("Supervisor", methods=[
 m("startCandidate:repro:", r"""| candFile reproFile outFile driver |
    candFile := self writeTemp: 'auto-cand' contents: candidateSource extension: 'st'.
    reproFile := self writeTemp: 'auto-repro' contents: reproSource extension: 'st'.
    outFile := self writeTemp: 'auto-out' contents: 'PENDING' extension: 'txt'.
    driver := self writeTemp: 'auto-driver'
        contents: (self driverForCandidate: candFile repro: reproFile out: outFile) extension: 'st'.
    LibC system: 'DRIVER=', driver fullName printString,
        ' sh /opt/pharo/scripts/shadow_run.sh >/dev/null 2>&1 &'.
    ^ outFile fullName""", True, params=['candidateSource', 'reproSource']),
 m("workspaceDir", r"""^ LiveKernel dataDir, '/workspace'""", True),
 m("writeTemp:contents:extension:", r"""| ref |
	LiveKernel ensureDir: self workspaceDir.
	ref := (self workspaceDir, '/', prefix, '-',
		Time millisecondClockValue printString, '-',
		(aString asString hash abs \\ 99991) printString, '.', ext)
			asFileReference.
	ref writeStreamDo: [ :s | s nextPutAll: aString asString ].
	^ ref""", True),
 m("testCandidate:repro:", r"""| candFile reproFile outFile driver rc outer |
	candFile := self writeTemp: 'cand' contents: candidateSource extension: 'st'.
	reproFile := self writeTemp: 'repro'
		contents: (reproSource ifNil: [ 'nil' ]) extension: 'st'.
	outFile := self writeTemp: 'out' contents: 'PENDING' extension: 'txt'.
	driver := self writeTemp: 'driver'
		contents: (self driverForCandidate: candFile repro: reproFile out: outFile)
		extension: 'st'.
	rc := LibC system: 'DRIVER=', driver fullName printString,
		' sh /opt/pharo/scripts/shadow_run.sh >/dev/null 2>&1; exit $?'.
	outer := [ STONJSON fromString: outFile contents ]
		on: Error do: [ :e | nil ].
	outer isNil ifTrue: [
		^ Dictionary new
			at: 'ok' put: false;
			at: 'rc' put: rc;
			at: 'detail' put: 'shadow output missing or malformed';
			yourself ].
	^ Dictionary new
		at: 'ok' put: (outer at: 'ok' ifAbsent: [ false ]);
		at: 'rc' put: rc;
		at: 'detail' put: outer;
		yourself""", True),
 m("driverForCandidate:repro:out:", r"""| d q installText |
	q := (Character value: 39) asString.
	installText := '/opt/pharo/kernel/install_eval.st' asFileReference contents.
	d := WriteStream on: String new.
	d nextPutAll: installText.
	d nextPutAll: '.'.
	d nextPutAll: String cr.
	d nextPutAll: '(Smalltalk globals at: #LiveKernel) shadowRun: (Dictionary new'.
	d nextPutAll: ' at: ', q , 'n', q , ' put: ', LiveKernel journal size printString, ';'.
	d nextPutAll: ' at: ', q , 'candidate', q , ' put: ', q ,
		candidatePath fullName, q , ';'.
	d nextPutAll: ' at: ', q , 'repro', q , ' put: ', q ,
		reproPath fullName, q , ';'.
	d nextPutAll: ' at: ', q , 'out', q , ' put: ', q ,
		outPath fullName, q , ';'.
	d nextPutAll: ' yourself).'.
	d nextPutAll: String cr , 'Smalltalk snapshot: false andQuit: true.'.
	^ d contents""", True),
]),

cls("VendingMachine", ivars="price paid", package="LiveKernelDemo", methods=[
 m("init", r"""price := 50.
	paid := 0"""),
 m("price:", r"""price := anAmount"""),
 m("insertCoin:", r"""paid := paid + anAmount"""),
 m("dispense", r"""paid >= price ifFalse: [ self error: 'insufficient credit' ].
	paid := paid - price.
	^ 'product'"""),
 m("make", r"""^ self new init""", True),
 m("repairContract", r"""^ 'A vending machine starts with price 50 and paid 0. insertCoin: adds credit. dispense must raise insufficient credit when paid < price; otherwise it subtracts price from paid and returns product. Repair behavior without changing the public protocol or instance variable layout.'""", True),
 m("repairExpectedErrors", r"""^ #('insufficient credit')""", True),
]),

cls("VendingMachineTests", package="LiveKernelDemo", methods=[
 m("verify:description:", r"""aBoolean ifFalse: [ self error: desc ]"""),
 m("testDispenseAfterCredit", r"""| vm |
	vm := VendingMachine make.
	3 timesRepeat: [ vm insertCoin: 20 ].
	self verify: vm dispense = 'product' description: 'should dispense'"""),
 m("testInsufficientCreditRaises", r"""| vm raised |
	vm := VendingMachine make.
	raised := false.
	[ vm dispense ] on: Error do: [ :e | raised := true ].
	self verify: raised description: 'should raise on empty credit'"""),
]),
]

from repair_methods import repair_classes
CLASSES.extend(repair_classes(cls, m))

INSTALL_TEMPLATE = """[ | defs cls |
  defs := (STONJSON fromString: '/opt/pharo/kernel/methods.json' asFileReference contents) at: 'classes'.
  defs do: [ :cd | | nm |
    nm := (cd at: 'name') asSymbol.
    (Smalltalk globals includesKey: nm) ifFalse: [
      Object subclass: nm instanceVariableNames: (cd at: 'ivars' ifAbsent: [ '' ])
        classVariableNames: '' poolDictionaries: ''
        package: (cd at: 'package' ifAbsent: [ 'LiveKernel' ]) ] ].
  defs do: [ :cd | | nm cls |
    nm := (cd at: 'name') asSymbol.
    cls := Smalltalk globals at: nm.
    (cd at: 'methods') do: [ :md | | src target |
      src := (md at: 'source') asString.
      target := ((md at: 'classSide') = true)
        ifTrue: [ cls class ] ifFalse: [ cls ].
      [ target compile: src ] on: Error do: [ :e |
        Transcript show: 'LK-COMPILE-FAIL ' , nm asString , ' ' ,
          (md at: 'sel') asString , ': ' , e asString; cr ].
      ] ] ] value. """

BOOT_TAIL = """
(Smalltalk globals at: #LiveKernel) boot.
(Smalltalk globals at: #QueueServer) loop"""


def build():
    d = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(d, "methods.json"), "w") as f:
        json.dump({"classes": CLASSES}, f, indent=1)
    with open(os.path.join(d, "install_eval.st"), "w") as f:
        f.write(INSTALL_TEMPLATE)
    with open(os.path.join(d, "boot_eval.st"), "w") as f:
        f.write(INSTALL_TEMPLATE + BOOT_TAIL)
    n = sum(len(c["methods"]) for c in CLASSES)
    print(f"kernel artifacts: {len(CLASSES)} classes, {n} methods")


if __name__ == "__main__":
    build()
