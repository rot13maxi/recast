[ | defs cls |
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
      ] ] ] value. 
(Smalltalk globals at: #LiveKernel) boot.
(Smalltalk globals at: #QueueServer) loop