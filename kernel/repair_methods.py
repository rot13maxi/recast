"""Smalltalk methods for incident-driven repair; consumed by gen_methods.py."""


def repair_classes(cls, m):
    return [cls('LLMRepairService', ivars='pending history', methods=[
        m('current', "^ Smalltalk globals at: #LKRepairService ifAbsent: [ nil ]", True),
        m('start', "Smalltalk globals at: #LKRepairService put: self new initialize", True),
        m('initialize', """pending := Dictionary new.
 history := (LiveKernel readJson: 'incidents.json' default: #()) asOrderedCollection.
 history do: [ :incident |
   (#('awaiting-model' 'verifying') includes: (incident at: 'status')) ifTrue: [
     incident at: 'status' put: 'cancelled-on-restart' ] ].
 self save.
 ^ self"""),
        m('save', "LiveKernel writeJson: 'incidents.json' value: history asArray"),
        m('incidents', '^ history asArray'),
        m('literal:', """(value isNil or: [ value == true or: [ value == false ] ]) ifTrue: [ ^ value printString ].
 (value isNumber or: [ value isString or: [ value isSymbol ] ]) ifTrue: [
   value printString size <= 1000 ifTrue: [ ^ value printString ] ].
 ^ nil""", params=['value']),
        m('capture:source:', """| context frames allowed target receiver selector method fingerprint state arguments repro request incident id sourceText contract tests |
 (LiveKernel configAt: 'automaticRepair' default: false) = true ifFalse: [ ^ nil ].
 pending size >= 8 ifTrue: [ ^ nil ].
 allowed := LiveKernel configAt: 'repairClasses' default: #('VendingMachine').
 context := error signalerContext.
 frames := OrderedCollection new.
 target := nil.
 1 to: 40 do: [ :index |
   context ifNotNil: [
     frames add: (LiveKernel truncate: context methodClass name asString, '>>', context selector asString to: 200).
     target isNil ifTrue: [
       ((allowed includes: context receiver class name asString)
         and: [ allowed includes: context methodClass name asString ]) ifTrue: [ target := context ] ].
     context := context sender ] ].
 target ifNil: [ ^ nil ].
 receiver := target receiver.
 (receiver class respondsTo: #repairContract) ifFalse: [ ^ nil ].
 (receiver class respondsTo: #repairExpectedErrors) ifTrue: [
   (receiver class repairExpectedErrors includes: error messageText) ifTrue: [ ^ nil ] ].
 selector := target selector.
 method := target method.
 fingerprint := receiver class name asString, '>>', selector asString, ':', method sourceCode hash asString, ':', error class name asString, ':', error messageText.
 (history anySatisfy: [ :entry | (entry at: 'fingerprint') = fingerprint ]) ifTrue: [ ^ nil ].
 state := Dictionary new.
 repro := WriteStream on: String new.
 repro nextPutAll: '| receiver | receiver := (Smalltalk globals at: #', receiver class name asString, ') basicNew.'.
 receiver class allInstVarNames withIndexDo: [ :name :index | | literal |
   literal := self literal: (receiver instVarAt: index).
   literal ifNil: [ ^ nil ].
   state at: name put: literal.
   repro nextPutAll: ' receiver instVarAt: ', index asString, ' put: ', literal, '.' ].
 arguments := target arguments collect: [ :value | self literal: value ].
 (arguments anySatisfy: [ :value | value isNil ]) ifTrue: [ ^ nil ].
 repro nextPutAll: ' receiver perform: #', selector asString, ' withArguments: {'.
 arguments withIndexDo: [ :literal :index |
   index > 1 ifTrue: [ repro nextPutAll: '. ' ]. repro nextPutAll: literal ].
 repro nextPutAll: '}. true'.
 sourceText := WriteStream on: String new.
 receiver class selectors sorted do: [ :sel |
   sourceText nextPutAll: (receiver class >> sel) sourceCode; cr; cr ].
 receiver class class selectors sorted do: [ :sel |
   sourceText nextPutAll: receiver class name asString, ' class>>'; nextPutAll: (receiver class class >> sel) sourceCode; cr; cr ].
 tests := WriteStream on: String new.
 LiveKernel testClassNames do: [ :name | | testClass |
   testClass := Smalltalk globals at: name asSymbol ifAbsent: [ nil ].
   testClass ifNotNil: [ testClass selectors sorted do: [ :sel |
     tests nextPutAll: name, '>>'; nextPutAll: (testClass >> sel) sourceCode; cr; cr ] ] ].
 contract := receiver class repairContract.
 id := AgentBridge nextId.
 request := Dictionary new
   at: 'id' put: id; at: 'kind' put: 'incident';
   at: 'problem' put: error class name asString, ': ', error messageText;
   at: 'target' put: receiver class name asString;
   at: 'selector' put: selector asString;
   at: 'repro' put: repro contents;
   at: 'operation' put: (LiveKernel truncate: operation to: 2000);
   at: 'stack' put: frames asArray; at: 'state' put: state;
   at: 'classSource' put: (LiveKernel truncate: sourceText contents to: 16000);
   at: 'testsSource' put: (LiveKernel truncate: tests contents to: 12000);
   at: 'contract' put: contract;
   at: 'journalSize' put: LiveKernel journal size; yourself.
 incident := Dictionary new
   at: 'id' put: id; at: 'target' put: (request at: 'target');
   at: 'selector' put: selector asString; at: 'problem' put: (request at: 'problem');
   at: 'fingerprint' put: fingerprint; at: 'status' put: 'awaiting-model';
   at: 'journalSize' put: LiveKernel journal size;
   at: 'started' put: Time millisecondClockValue;
   at: 'request' put: request; yourself.
 history add: incident. pending at: id put: incident. self save.
 LiveKernel writeJson: 'heal/req-', id asString, '.json' value: request.
 ^ id""", params=['error', 'operation']),
        m('finish:status:detail:', """incident at: 'status' put: status; at: 'detail' put: detail.
 incident removeKey: 'request' ifAbsent: [ ].
 incident removeKey: 'started' ifAbsent: [ ].
 pending removeKey: (incident at: 'id') ifAbsent: [ ].
 self save""", params=['incident', 'status', 'detail']),
        m('allowedSource:for:', """| ast statements node method |
 ast := RBParser parseExpression: source.
 statements := ast isSequence ifTrue: [ ast statements ] ifFalse: [ { ast } ].
 statements size = 1 ifFalse: [ ^ false ].
 node := statements first.
 (node isMessage and: [ node selector = #compile: ]) ifFalse: [ ^ false ].
 (node receiver isVariable and: [ node receiver name asString = (incident at: 'target') ]) ifFalse: [ ^ false ].
 (node arguments size = 1 and: [ node arguments first isLiteralNode ]) ifFalse: [ ^ false ].
 node arguments first value isString ifFalse: [ ^ false ].
 method := RBParser parseMethod: node arguments first value.
 ^ method selector asString = (incident at: 'selector')""", params=['source', 'incident']),
        m('poll', """pending values asArray do: [ :incident |
   [ self advance: incident ] on: Error do: [ :error |
     self finish: incident status: 'repair-error' detail: error asString ] ]"""),
        m('advance:', """| response file request source decision verdict result |
 (LiveKernel configAt: 'automaticRepair' default: false) = true ifFalse: [
   ^ self finish: incident status: 'cancelled' detail: 'automatic repair disabled' ].
 LiveKernel journal size = (incident at: 'journalSize') ifFalse: [
   ^ self finish: incident status: 'stale' detail: 'code changed during investigation' ].
 request := incident at: 'request'.
 (incident at: 'status') = 'awaiting-model' ifTrue: [
   (Time millisecondClockValue - (incident at: 'started')) > 300000 ifTrue: [
     ^ self finish: incident status: 'supervisor-timeout' detail: 'no decision within 300 seconds' ].
   file := (AgentBridge healDir, '/resp-', (incident at: 'id') asString, '.json') asFileReference.
   file exists ifFalse: [ ^ nil ].
   response := STONJSON fromString: file contents.
   (response at: 'id' ifAbsent: [ nil ]) = (incident at: 'id') ifFalse: [
     ^ self finish: incident status: 'bad-supervisor-response' detail: 'response id mismatch' ].
   decision := response at: 'decision' ifAbsent: [ 'invalid' ].
   (#('expected' 'insufficient-evidence') includes: decision) ifTrue: [
     ^ self finish: incident status: decision detail: (response at: 'reason' ifAbsent: [ '' ]) ].
   source := response at: 'source' ifAbsent: [ nil ].
   (decision = 'repair' and: [ source isString and: [ source isEmpty not ] ]) ifFalse: [
     ^ self finish: incident status: 'bad-supervisor-response' detail: (response at: 'error' ifAbsent: [ 'invalid repair decision' ]) ].
   (self allowedSource: source for: incident) ifFalse: [
     ^ self finish: incident status: 'out-of-scope' detail: 'automatic repairs must compile only the failing application method' ].
   incident at: 'source' put: source;
     at: 'reason' put: (response at: 'reason' ifAbsent: [ '' ]);
     at: 'outPath' put: (Supervisor startCandidate: source repro: (request at: 'repro'));
     at: 'status' put: 'verifying'; at: 'started' put: Time millisecondClockValue.
   self save. ^ nil ].
 file := (incident at: 'outPath') asFileReference.
 (Time millisecondClockValue - (incident at: 'started')) > 150000 ifTrue: [
   ^ self finish: incident status: 'shadow-timeout' detail: 'shadow did not finish' ].
 file exists ifFalse: [ ^ nil ].
 verdict := [ STONJSON fromString: file contents ] on: Error do: [ :error | nil ].
 verdict ifNil: [ ^ nil ].
 (verdict at: 'ok' ifAbsent: [ false ]) = true ifFalse: [
   ^ self finish: incident status: 'shadow-verify-failed' detail: verdict ].
 result := LiveKernel eval: (incident at: 'source').
 (LiveKernel evalResultFailed: result) ifTrue: [
   ^ self finish: incident status: 'swap-failed' detail: result ].
 LiveKernel noteHealed: (Dictionary new at: 'id' put: (incident at: 'id');
   at: 'target' put: (incident at: 'target'); at: 'source' put: (incident at: 'source');
   at: 'swapResult' put: result; at: 'automatic' put: true; yourself).
 self finish: incident status: 'repaired' detail: verdict""", params=['incident']),
    ])]
