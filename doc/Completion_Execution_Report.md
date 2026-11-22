# Esecuzione del piano ECC — 22 ottobre 2026

Implementazione nel progetto originale `C:/git/FastSkillRetrieval`, usando la copia
della chat come staging dopo confronto degli hash. Le sole differenze iniziali erano
due file generati `.egg-info`. Baseline: 93 file inventariati, 100 manuali storici
verificati SHA-256, 18 voci di evidenza storica acquisite. Nessun commit, push, PR,
installazione globale o modifica automatica del client.

Metodo ECC: orch-add-feature/orch-pipeline, TDD Workflow ed Eval Harness. Il piano
esistente è l'approvazione di implementazione. Lane separate hanno sviluppato
profili/ricevute, schemi/dataset e retrieval/benchmark. La revisione indipendente
di contratti/processi/costi ha prodotto riproduttori e un riesame finale senza
criticità alte concrete residue. Il router plan-orchestrate è stato consultato,
senza presentarlo come esecutore di slash command.

## Incrementi e gate

| C | Risultato | Stato del gate |
|---|---|---|
| C01 | manifest baseline, confronto copie, hash storico e backup dei soli file modificati | G01 verificato |
| C02 | protocollo v1 separato dal v2, due JSON Schema, metriche/null, budget/approvazioni | software verificato; G02 dipende anche dalla revisione dataset |
| C03 | profili A/B/C, catalogo B nel prompt, allowlist/denylist, probe zero-uso, misura locale corretta | fixture verificate; G03 host pending, Claude non avviato |
| C04 | 24 gruppi IT/EN, tre gruppi pilot separati, audit risorse, storico 100/20 conservato | nuova revisione umana pending; G02 parziale |
| C05 | policy graph/lexical nel fingerprint, ablazione e classificazione di 24 finding | G04 software verificato; qualità non risolta, entrambi 20/38 sullo storico |
| C06 | piano offline, journal/SQLite, tentativi, riserve, timeout, resume/retry espliciti | G05 verificato con client finti/processi innocui |
| C07 | parser correlato con hash body e ruoli, output/replay, review operatore | G06 fixture e G07 null/failure verificati; misura catalogo host indisponibile |
| C08 | matrice per funzione, dispatch/preload/enforcement non attestati | diagnosi completata; G08 chiuso e P7 aperto |
| C09 | prova assistita richiesta dall'utente nella sessione flat | sei formulazioni/tre gruppi, un tentativo; G09 Claude non raggiunto |
| C10a | protocollo storico A/B/C senza mutare query/attesi/fingerprint | confronto host pending; nessun risparmio certificato |
| C10b | proposta holdout pubblica separata dal development | non eseguita; G10 pending |
| C11 | benchmark attraverso ingestion/publication pubbliche, supplementi e regressioni | risultati e limiti nel bundle; non si presume il superamento della soglia |
| C12a | guida, wheel/sdist, installazione isolata dal lock | verifiche locali nel bundle; G12 host/preflight parziale |
| C12b | chiusura sperimentale distinta dalla consegna locale | pending, richiede confronto e revisione umana |

La richiesta successiva dell'utente sostituisce per ora Claude con attività svolte
dalla sessione inclusa nel piano flat. Questo cambia la superficie della prova,
non gli attesi o i criteri storici. Le tre attività sono test Go, struttura FastAPI
e scouting delle skill, ciascuna in IT/EN. Sei search e sei load sono riusciti;
i corpi letti sono stati verificati contro le revisioni sorgenti. Elaborati:
[IT](evaluations/Session_Flat_Assisted_IT.md) e
[EN](evaluations/Session_Flat_Assisted_EN.md). Stato `produced_ungraded`:
l'autore agente non si attribuisce revisione umana o applicazione attestata.
Non sono le 19 prove A/C+B del pilota. Costi, token e riduzioni host restano null.
Nessun processo esterno del modello è stato avviato.

## Matrice storica P/CR

P0–P5 e P8 conservano il core e le regressioni; P6/P7 restano subordinati alle
primitive host. P9 locale è aggiornato dalle verifiche di questa esecuzione;
P9 utilità non è chiuso dalla consegna di manuali.

| CR | Evidenza e limite |
|---|---|
| CR01 | validazione descrittori: regressioni locali |
| CR02 | alias/deduplicazione: regressioni e policy esplicita |
| CR03 | tentativi persistenti: registry e runner distinti |
| CR04 | request ledger/replay: regressioni e nuova prova concorrente |
| CR05 | stale/tamper: regressioni e rifiuto nel benchmark |
| CR06 | gate host chiuso, nessuna invocazione nativa simulata |
| CR07 | dipendenze/top-K: regressioni, hub e cicli separati |
| CR08 | conflitti: regressioni; enforcement host non attestato |
| CR09 | budget byte/sessione: regressioni/concorrenza; costi modello separati |
| CR10 | pubblicazione interrotta/rollback: regressioni e failure preservati |
| CR11 | quarantena/consumi: regressioni; nessun restore dello stato personale |
| CR12 | path/input: regressioni; task dichiarativi, nessuna sandbox host dichiarata |
| CR13 | registry senza query/file nei log; valutazione in bundle separato |
| CR14 | parser conservativo/self-report unknown; native receipt condizionale |
| CR15 | smoke CLI/MCP sorgente e wheel, superficie Claude distinta |
| CR16 | corpus conservato e misure locali aggiornate; utilità/30% non dimostrati |

Una build non sostituisce i gate di scala/preflight. La Definition of Done
sperimentale non viene raggiunta dalla prova assistita della sessione.

## Correzioni e verifiche

I contratti e il runner sono stati introdotti dopo RED per moduli assenti. Le
verifiche coprono split per gruppo, hash, conteggi, null, approvazioni, limiti,
crash dopo riserva, retry, costi sconosciuti, budget cumulativi e output parziale.
Il v2 storico rimane compatibile. La review ha fatto correggere il rilascio indebito
di riserva su FileNotFoundError successivo all'attività, la somma di costi/retry,
il cleanup del processo su timeout e la perdita dell'output necessario al grading.
Modello effettivo e versione osservati vengono confrontati con i pin.

Il supplemento concorrente ha riprodotto due `catalog_unavailable`, come il nuovo
test `test_registry_concurrency.py`. Ogni operazione addebita un budget durevole:
`BEGIN IMMEDIATE` prima del savepoint riserva lo scrittore prima dello snapshot,
impedendo il deadlock di promozione read->write. Test e venti regressioni su
sicurezza/pubblicazione/retention sono passati; il supplemento successivo ha
osservato entrambe le query riuscite. Il report del fallimento resta conservato.

Suite intermedia: 315 passati, uno skip, 176,70 secondi. Lo skip è quello symlink
Windows già presente. Suite/copertura/build/smoke finali sono registrati nel bundle
con versioni e hash effettivi. Nessun type checker o giudice LLM del benchmark è
stato aggiunto. I limiti della copertura vengono riportati senza promuovere prove
fixture a risultati host.

## Bundle e recupero

Bundle autorevole: `.work/completion-implementation-20261022` nel progetto originale.
Contiene `baseline-source-manifest.json`, `historical-corpus-verification.json`,
`historical-evidence-manifest.json`, `original-backup/`, `sync-receipt.json`, lock,
build e risultati. Le prove assistite sono in `.work/completion-session-flat-20261022`.
La ricevuta di hash non è uno SHA Git. Configurazioni personali soltanto hashate;
nessuna copia delle credenziali nei pacchetti. Prima della distribuzione del bundle
servono redazione e revisione umana.

Per recupero software usare i soli file enumerati e i backup corrispondenti;
i file aggiunti sono distinguibili dai sostituiti. Disattivare il nuovo profilo
locale se necessario. Non ripristinare un vecchio database sopra quello corrente:
quarantene, tentativi e consumi non devono retrocedere. Riprendere i run dal DB/journal,
conservando i fallimenti e le riserve ancora a costo sconosciuto.

## Correzione della costruzione del grafo a 5.000 nodi

La prova pubblica ha dimostrato un timeout del builder monolitico, anche dopo aver
completato gli snapshot. Il nuovo builder conserva cursori per nodi/alias e archi,
lavora in un catalogo ombra non visibile e pubblica solo allo scambio finale atomico.
Non persiste i manuali grezzi nella fase provvisoria; valida nuovamente gli snapshot.
Rebuild e pubblicazione si riprendono senza perdere il grafo attivo. Test mirati:
17 passati, inclusa la mancata attivazione di un catalogo parziale e la firma invariata
di un grafo ricostruito. Review indipendente senza criticità alte di atomicità/FK/exposure;
il limite distinto dei batch snapshot/grafo è documentato nella guida.

L'indice derivato dei descrittori velocizza la selezione durante le pubblicazioni,
senza modificare tabelle, riferimenti o versioni di schema. I batch adattivi del
benchmark conservano nei risultati tutti i timeout precedenti. La verifica di budget
concorrente è stata corretta per controllare i byte effettivamente emessi e il rifiuto
all'esaurimento: il limite non è una quota arbitraria di una sola emissione. I gruppi
con dipendenze già caricate conservano il contratto esistente.

## Verifica finale della consegna locale

La suite finale registra **329 test passati, uno skip Windows già presente e zero
errori**, in 233,42 secondi; copertura complessiva **81,48%**. Ruff passa. Le sole
modifiche successive ai test su `graph.py` e `registry.py` sono di formattazione:
`format-equivalence.json` ne verifica l'identità AST rispetto ai sorgenti misurati,
conservati in `coverage-source/`; anche le regressioni mirate successive passano.

Wheel e sdist sono in `release-final/`. L'installazione isolata usa il lock
offline con verifica degli hash; gli smoke MCP installati espongono correttamente
10 strumenti registry e 8 legacy. Le distribuzioni escludono stato runtime e
configurazioni personali. Il controllo conclusivo ha individuato ed escluso dal
sdist un file locale di hook sotto `doc/.claude/`; il pacchetto è stato rigenerato.
`verification-summary.json`, `final-source-manifest.json`
e `DELIVERY-SHA256.json` registrano risultati e integrità della consegna.

Il benchmark pubblico finale misura p95 **94,9445 ms a 100 nodi** e **147,052 ms a
5.000 nodi/5.000 archi** (p99 153,0579 ms), con tutte le 100 query riuscite per
scala. La prova a 5.000 nodi passa tutti i controlli funzionali previsti; il
supplemento osserva entrambe le connessioni concorrenti riuscite. Il picco RSS del
processo è 101.515.264 byte e include import e preparazione. L'ultimo segmento di
preparazione ripresa dura circa 7,7 minuti: il tempo complessivo dei tentativi
precedenti resta sconosciuto. Questi numeri descrivono il grafo sintetico sparso
e cache OS non controllata; latenza avversaria densa a 5.000 nodi e cache disco OS
fredda restano non misurate.

Su richiesta dell'utente, sei prove assistite IT/EN sono state prodotte nella
sessione flat corrente e conservate in `doc/evaluations/Session_Flat_Assisted_*.md`.
Sono esempi di sviluppo non valutati, con contesto già esposto al catalogo: non
sostituiscono un confronto cieco A/B/C. Nessun processo modello Claude è stato
invocato. Costi e token host non osservati restano null. L'ablazione storica resta
20/38 essenziali sia graph sia lexical; non dimostra un miglioramento di qualità.

La parte software locale è consegnata. Restano aperti revisione umana degli attesi,
validazione comparativa sul client e gate di dispatch/preload nativo non verificati.
Il risparmio del 30% e il completamento empirico dell'intero piano non sono dimostrati.
