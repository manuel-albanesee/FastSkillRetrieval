# FastSkillRetrieval

Implementazione locale **v0.3** in Python 3.13: registro versionato per skill, subagent e
workflow, ricerca SQLite/FTS5, piani di dipendenze e interfacce CLI/MCP `stdio`.
Il nuovo registro si attiva con `--registry`; il percorso legacy v0.1 resta disponibile.
L'attivazione nativa rimane subordinata a una prova attendibile del runtime.
Le sorgenti originali non vengono modificate. Il framework non esegue script o modelli LLM.

Il percorso operatore sperimentale è separato dal core: `python -m
fastskillretrieval.experiment_cli` valida protocolli, prepara run offline e gestisce prove
esplicite con budget. Guida: [completamento ECC](Completion_Operator_Guide.md).
Le prove svolte nella sessione flat sono rendicontate separatamente in
[italiano](evaluations/Session_Flat_Assisted_IT.md) e
[inglese](evaluations/Session_Flat_Assisted_EN.md); non certificano il confronto
Claude A/B/C o un risparmio del 30%.

Il client finale previsto è Claude Code CLI. Il 18 ottobre 2026 la matrice host ha verificato
tre sessioni reali MCP registry, con 27 chiamate totali, e tre esecuzioni del ponte nativo
operatore su Claude Code 2.1.302. Il runtime non certifica il preload generalizzato né
l'enforcement dei permessi: il dispatch automatico del planner resta deliberatamente assente;
vedere
[doc/Capability_Host_Verification.md](Capability_Host_Verification.md).

## Installazione nel workspace

Ambiente iniziale: Windows/PowerShell, Python 3.13.14. Le versioni dirette sono fissate in
`pyproject.toml`; `uv.lock` blocca anche le dipendenze transitive con hash degli artefatti.
Non sono necessarie chiavi API per il framework.

```powershell
uv sync --frozen --extra dev
$python = (Resolve-Path .venv/Scripts/python.exe).Path
& $python -m fastskillretrieval --help
```

L'ambiente `.venv` è locale al repository. Non occorrono installazioni globali; il repository pubblico è su GitHub
(`manuel-albanesee/FastSkillRetrieval`).
L'entry point installato `fastskillretrieval` chiama lo stesso ingresso di
`python -m fastskillretrieval`. Su questa macchina Windows il launcher `.exe` generato
ha ricevuto `Accesso negato`; l'avvio verificato è tramite `python -m`, usato in tutti
i comandi sotto e nella configurazione MCP.

## Prova manuale legacy v0.1

Da PowerShell nella radice del repository:

```powershell
$python = (Resolve-Path .venv/Scripts/python.exe).Path
$root = (Resolve-Path examples/skills).Path
$baseArgs = @('-m', 'fastskillretrieval', '--source-root', $root,
              '--state', "$PWD/.fsr", '--session', 'manual-demo')
$inventory = (& $python @baseArgs inventory --refresh) | ConvertFrom-Json
$skill = $inventory.items[0]
& $python @baseArgs inspect $skill.id $skill.version
& $python @baseArgs submit $skill.id $skill.version examples/markdown-candidate.json
& $python @baseArgs publish
$found = (& $python @baseArgs search 'controllare Markdown') | ConvertFrom-Json
$reference = $found.candidates[0].reference | ConvertTo-Json -Compress
& $python @baseArgs load $reference
& $python @baseArgs resource $reference references/checklist.md
& $python @baseArgs diagnostics
```

Il file della risorsa contiene `FSR-MARKDOWN-CHECK-01`. Ripetere `publish` senza modifiche
non crea un nuovo catalogo. Ripetere `submit` consuma invece un altro tentativo: non è un
comando di sola lettura. Ogni versione sorgente ha al massimo due tentativi persistenti.

Una radice autorizzata contiene una skill per sottodirectory immediata, ciascuna con
`SKILL.md`. Il percorso della radice è fissato all'avvio, non ricevuto da un tool MCP.
Usare una directory di stato dedicata per ciascuna radice. Una radice differente con lo
stesso stato viene rifiutata. Origine e percorso relativo fanno parte dell'identità.

`inventory` legge lo stato registrato; soltanto `inventory --refresh` scandisce i file.
Sono disponibili `--offset` e `--limit` per paginare. La scansione è limitata a 5.000 skill
e a 100 versioni nuove/modificate per chiamata; un lotto eccedente viene annullato. Per
questa prima prova usare un catalogo piccolo; la gestione di lotti grandi resta da estendere.

## Metadati e pubblicazione

Lo schema eseguibile è in `contracts.py`; un esempio completo è in
`examples/markdown-candidate.json`. Sono ammessi soltanto descrizione, alias, capacità,
condizioni d'uso, esclusioni e tecnologie. Ogni capacità richiede azione, oggetto, ambito e
un passaggio presente in un file del pacchetto. Identità, permessi e abilitazione non possono
essere impostati dalla candidata. Il punteggio 100 indica conformità strutturale, non una
certificazione semantica o di sicurezza. La revisione del significato resta umana.

L'inventario rende visibili `discovered`, `needs_revision`, `blocked`, `validated`,
`published` e `missing`, con motivo e tentativi. Le nuove versioni non validate non entrano
nel retrieval. Una pubblicazione prepara snapshot completi prima di attivare il catalogo
in una transazione SQLite. Un arresto può lasciare snapshot non attivi, senza pubblicarli.
Le riscritture dei metadati producono cataloghi distinti; i metadati delle pubblicazioni
precedenti vengono conservati separatamente.

Se una nuova versione fallisce, una precedente già pubblicata resta disponibile soltanto
con snapshot integro e abilitazione attiva. Le modifiche alle risorse cambiano l'hash
dell'intero pacchetto. Lettura e caricamento verificano il manifesto completo; una manomissione
restituisce `stale_reference`. Anche una sorgente rimossa può lasciare disponibile il vecchio
snapshot: il ritiro richiede un comando esplicito dell'operatore.

```powershell
& $python @baseArgs set-enabled $skill.id false
& $python @baseArgs publish
```

Il blocco impedisce subito nuovi caricamenti, anche con riferimenti vecchi; il comando
inverso con `true` e una nuova pubblicazione riabilita la skill. Sorgenti e storico restano.

## Ricerca e budget

FTS5/BM25 usa pesi 5/4/4/3/2 per descrizione, capacità, condizioni, alias e tecnologie;
gli spareggi seguono l'ID. Le esclusioni non sono indicizzate come segnali positivi.
L'input viene trasformato in termini letterali quotati, non interpretato come sintassi FTS.
`C`, `C++` e `C#` restano distinti. Non è implementata comprensione delle negazioni naturali:
usare `--technology`, `--exclude-id` e `--explicit-id` per vincoli espliciti.
Non si usano popolarità o punteggi di validazione per favorire il ranking.

Limiti predefiniti: cinque schede, 3.600 byte per le schede, 64 KiB per un gruppo di
caricamento/risorse e 256 KiB per sessione; risposta massima 64 KiB. È usata una stima
conservativa pari ai byte UTF-8 della risposta JSON completa. Non è un conteggio del tokenizer
Claude, del suo involucro MCP o dell'intero prompt. Il margine riduce la capacità utile.
I limiti sono configurabili con `--limits percorso.json`, un oggetto di interi positivi
con i nomi mostrati da `diagnostics`. Per i candidati il massimo operativo resta cinque.

Mantenere lo stesso `--session`/`session_id` nello stesso contesto. I contatori persistono;
non vengono azzerati supponendo una compattazione. Una nuova sessione rappresenta un nuovo
contesto. Il primo caricamento blocca la versione alla sessione; una versione incompatibile
richiede una nuova sessione. Il materiale emesso non prova che il modello lo abbia applicato.
Anche le letture per preparazione e le ripetizioni consumano budget. Le istruzioni non vengono
troncate. Una risposta che non entra viene rifiutata senza attivarne gli effetti transazionali.
Le sole risposte di rifiuto contengono overhead di protocollo inevitabile: è conteggiato
anche dopo esaurimento e può superare la soglia stimata; non contengono nuovo materiale.

Prima del caricamento si stima il costo dell'intero pacchetto, comprese tutte le risorse UTF-8,
anche facoltative: un piano troppo grande viene rifiutato in anticipo. Le emissioni ripetute
della stessa skill/versione nella sessione condividono il tetto di 64 KiB, senza frazionamenti
per aggirarlo. Una risorsa richiede prima un caricamento riuscito. Le risorse binarie non
vengono emesse. L'uso con dipendenze obbligatorie è rinviato.

Non vengono persistiti testi delle ricerche o log di contenuti. Si conservano metadati,
pacchetti, contatori aggregati di esito e costo stimato per sessione. Non essendoci log
persistenti di eventi, non è necessaria una rotazione dei log in questa fase.

## MCP e Claude Code diretto

Il server usa l'SDK ufficiale MCP Python 1.29.0, trasporto `stdio`. Nessuna porta HTTP.
I tool sono: `inventory_skills`, `inspect_source`, `submit_candidate`, `publish_catalog`,
`search_skills`, `load_skill`, `read_skill_resource`, `catalog_diagnostics`.
La generazione delle candidate avviene nella sessione dell'agente, non nel server.

Per generare un catalogo fixture e una configurazione già completa di percorsi assoluti:

```powershell
& $python scripts/prepare_claude_test.py
claude --strict-mcp-config --mcp-config .work/claude-test/mcp-config.json
```

Il secondo comando avvia direttamente Claude Code, senza launcher o registrazioni globali.
Incollare il contenuto di `.work/claude-test/prompt.txt` nella nuova sessione. Per un'altra
radice usare `examples/mcp-config.template.json` sostituendo i tre segnaposto con percorsi
assoluti. La creazione della fixture non avvia Claude e non consuma quota LLM.

Chiedere al client di cercare `Markdown`, caricare il riferimento e leggere
`references/checklist.md`, usando lo stesso `session_id`. Verificare il marker della risorsa.
Poi provare una ricerca senza corrispondenza e una risorsa `../outside.txt`: gli esiti attesi
sono rispettivamente `no_match` e `policy_denied`. Interrompere la sessione per ripristinare
la normale configurazione di avvio. La prova non modifica le skill native.

La configurazione segue la [documentazione ufficiale Claude Code MCP](https://code.claude.com/docs/en/mcp).
Il [comportamento delle skill native](https://code.claude.com/docs/en/skills) resta un percorso
distinto da questa emissione di testo. `allowed-tools`, fork, hook, dipendenze dichiarate nel
frontmatter e sostituzioni dinamiche non sono supportati: la preparazione li blocca invece
di simulare l'invocazione nativa. Il controllo è prudenziale e non un parser completo di ogni
estensione futura. Una sorgente ostile resta dato non attendibile; la sessione host conserva
i propri strumenti e permessi.

## Integrazione con Claude Code e Codex (hook di prompt)

Oltre al server MCP, FSR può suggerire strumenti installati prima che la richiesta arrivi al
modello, con un hook `UserPromptSubmit` in sola lettura (richiede Node.js 22.5 o successivo).

```powershell
fastskillretrieval install --agent claude      # oppure --agent codex
fastskillretrieval init-index --agent claude --if-stale
```

- `install` registra l'hook in `~/.claude/settings.json` (Codex: `~/.codex/hooks.json`), aggiunge
  `/fsr-init-index` e costruisce l'indice; `--no-index` salta la costruzione. È idempotente, conserva
  un solo `.fsr-backup` e `uninstall` rimuove soltanto ciò che ha creato. Codex chiede di
  approvare l'hook una volta da `/hooks`. Dopo l'installazione, alla prima sessione (Codex: al
  primo prompt) l'hook mostra una sola volta, solo all'utente e senza costo in token, un avviso
  in chat: FSR è installato e bisogna eseguire `/fsr-init-index` per attivare l'iniezione
  automatica di agent e skill. `FSR_MODE=off` lo sopprime.
- `init-index` indicizza skill, agent e comandi utente, di progetto e dei plugin attivi, copiati
  in `~/.fsr/sources/<agent>/`; il registro va in `~/.fsr/index/<agent>/`. Con `--if-stale` non
  ricostruisce se l'insieme installato è invariato. Nessun modello viene chiamato.
- L'hook nomina fino a tre risultati. Il testo completo di una skill o di un comando è iniettato
  solo per i match **forti** (copertura dei termini della richiesta almeno `FSR_STRONG_COVERAGE`,
  0,3 di default); i match deboli sono solo nominati. Il nome della definizione pesa nel ranking e i
  token puramente numerici della richiesta sono ignorati.
- Lingua: FSR non traduce nomi né descrizioni. L'hook aggiunge alla sola ricerca i termini inglesi
  equivalenti alle parole italiane della richiesta (`semplifica` aggiunge `simplify`), senza
  contarli nella copertura dei match forti, così gli agent descritti in inglese si trovano da
  richieste in italiano.
- Agent: una seconda query limitata agli agent aggiunge fino a due agent che i primi tre risultati
  globali avrebbero escluso (stessa soglia assoluta). Prima dell'indicizzazione `agent_quality.py`
  (deterministico, senza modelli) ripulisce le descrizioni e aggiunge agli *alias* i termini
  dell'altra lingua e radici inglesi leggere, senza toccare la descrizione; la versione delle
  regole fa parte della firma dell'indice, quindi `--if-stale` ricostruisce dopo una modifica.
- Il mod `fsr-agent-listing` riduce l'elenco degli agent di Claude Code ai soli integrati; la
  descrizione degli agent pertinenti è iniettata una volta per sessione. `--no-agent-listing`
  lo disattiva. Gli agent esclusi dall'elenco restano invocabili per nome.
- `hide-skills [--restore]` imposta `disable-model-invocation: true` sulle skill dei plugin, con
  ripristino esatto; va rilanciato dopo un aggiornamento dei plugin. `hide-agents --plugin NOME
  [--keep plugin:agent] [--dry-run] [--restore]` scrive regole `Agent(...)` in `permissions.deny`
  con backup byte per byte.
- Variabili: `FSR_HOME`, `FSR_MODE` (`inject`, `suggest`, `off`), `FSR_INJECT_BODY`,
  `FSR_BODY_HITS`, `FSR_BODY_LIMIT`, `FSR_INJECT_LIMIT`, `FSR_STRONG_COVERAGE`, `FSR_STRONG_CAP`,
  `FSR_AGENT_DESC`, `FSR_UI`. Valori predefiniti nel [README](../README.md).
- `scripts/hook_eval.cjs` esegue richieste etichettate (`scripts/hook_eval_cases.json`) per
  tarare le soglie; `scripts/agent_eval.cjs [casi.json] [--verbose]` misura recall@3, top-1 e
  falsi positivi sugli agent (i casi in `scripts/agent_eval_cases.json` usano gli agent della
  macchina del maintainer: adattali ai tuoi). Un hook in errore non produce output e non blocca mai il prompt.

## Capability Retrieval v0.2 (opt-in)

La versione 0.2 aggiunge un registry locale per capability tipizzate (`skill`, `subagent` e
`workflow`). È **opt-in**: aggiungere `--registry` seleziona il nuovo stato e i nuovi
contratti; senza il flag restano attivi CLI, database e otto tool MCP v0.1. Il registry non
avvia LLM, non esegue istruzioni o script e non trasforma una discovery in autorizzazione
nativa del client.

```powershell
$python = (Resolve-Path .venv/Scripts/python.exe).Path
$root = (Resolve-Path examples/skills).Path
$v2 = "$PWD/.fsr-v2"
$capabilityArgs = @('-m', 'fastskillretrieval', '--registry', '--source-root', $root,
                    '--state', $v2, '--session', 'capability-demo')

$discovered = (& $python @capabilityArgs discover) | ConvertFrom-Json
$item = $discovered.items[0]
& $python @capabilityArgs inspect $item.id $item.source_revision
& $python @capabilityArgs submit $item.id $item.source_revision examples/markdown-candidate.json
& $python @capabilityArgs publish
$found = (& $python @capabilityArgs search Markdown --kind skill --request-id demo-1) | ConvertFrom-Json
$reference = $found.candidates[0].reference | ConvertTo-Json -Compress
$plan = (& $python @capabilityArgs plan $reference --mode prepare_use) | ConvertFrom-Json
& $python @capabilityArgs prepare $plan.plan_id
& $python @capabilityArgs load $reference
& $python @capabilityArgs resource $reference references/checklist.md
& $python @capabilityArgs diagnostics
```

`discovery` e `prepare_use` selezionano o rivalidano un risultato e un piano, ma nessuna API
di piano esegue azioni. Per una skill semplice, il caricamento assistito verificato è
`dry_run=false` e può essere consentito come sola lettura. L'attestazione host resta assente
per le azioni native: quelle rimangono fail-closed e il relativo piano è bloccato in dry run.
L'emissione del corpo non certifica che Claude lo abbia applicato. Per subagent e workflow il
registry descrive e pianifica, ma non esegue una delega o un workflow.

Con `--registry` il server espone soltanto dieci tool: `discover_capabilities`,
`inspect_capability_source`, `submit_capability_descriptor`, `publish_catalog`,
`search_capabilities`, `plan_capabilities`, `prepare_capability_use`, `load_capability`,
`read_capability_resource` e `registry_diagnostics`. Le istruzioni MCP indicano lo stesso
percorso di ricerca e caricamento. Non sono esposti contemporaneamente i tool legacy;
senza `--registry` resta disponibile l'interfaccia v0.1 separata. Il template
`examples/mcp-config.template.json` seleziona il registry e richiede uno stato registry dedicato.
Le prove storiche sulla superficie da 17 tool non sostituiscono la verifica di questa modifica.
La prova diretta su Claude
Code 2.1.302 ha osservato separatamente le sei chiamate MCP legacy e una delega nativa minima
`Agent`; nessuna delle due prove certifica il dispatch del planner, il preload di skill,
hook/policy o l'enforcement di `tools: []`. I dettagli e le ricevute sono in
[doc/Capability_Host_Verification.md](Capability_Host_Verification.md).

### Consegna dei manuali e metadata descrittivi

Le annotazioni `metadata` (oggetto YAML), `license`, `homepage`, `origin` e `author`
non richiedono invocazione nativa. Il parser conserva questi dati, controlla YAML e
credenziali e consegna il file originale completo, senza tagli o riscritture. Anche
`argument-hint` e il campo ECC `tools` sono suggerimenti descrittivi conservati come dati:
non aggiungono strumenti o permessi. `tools` non viene tradotto nel campo nativo
`allowed-tools`. Campi operativi come `allowed-tools`, `context`, `model` e sostituzioni
dinamiche effettive restano bloccati nel caricamento assistito.

Il controllo distingue un comando nativo da punteggiatura in codice Markdown:
`assert_eq!` e il segno `!` nei conventional commit non sono comandi. I nomi di variabili
isolati in codice inline sono riferimenti letterali; percorsi o comandi inline che usano
variabili host richiedono ancora il runtime nativo. I blocchi di codice sotto intestazioni
esplicite di controesempio (`Anti-Patterns`, `Counterexamples`, `Bad Examples`) vengono
letti come esempi da evitare. Un titolo dello stesso livello o superiore termina quel contesto.
I veri placeholder shell inline e i fence di injection rimangono bloccati anche nei
controesempi. Il rilevatore è conservativo e non è un emulatore completo del renderer Claude.

I nuovi limiti consentono manuali più grandi e aumentano il contesto potenzialmente emesso:
non sono una misura del risparmio del 30%. Restano configurabili con `--limits`;
per esempio `{"load":4000,"period":12000}` ripristina i precedenti tetti di consegna.
Un gruppo più grande del limite viene rifiutato integralmente, comprese le risorse opzionali.

I manifesti pubblicati nei vecchi registry conservano la decisione di attivazione originale.
Per applicare la nuova classificazione usare **uno stato registry nuovo**, ripetendo
discovery, validazione dei descrittori e pubblicazione. `discover` sullo stato precedente
non riscrive i manifesti delle revisioni e dei cataloghi già pubblicati. Conservare lo
stato precedente e puntare il client al nuovo solo dopo la verifica.

Per verificare la consegna delle 100 skill della fixture con hash fissati, senza modello:

```powershell
& $python scripts/verify_real_skill_delivery.py `
  --source-root <fixture>/sources --manifest <fixture>/fixture-manifest.json `
  --state .work/delivery-registry-new --output .work/delivery-report-new.json
```

Stato e report devono essere nuovi. Il comando pubblica descrittori ricavati dalle sorgenti
nel solo stato indicato, verifica i byte di ogni manuale compatibile e prova i cinque più
grandi nella stessa sessione. È una verifica di consegna, non di rilevanza o qualità con Claude.

### Grafo delle skill, sul modello Teseo

Il registry mantiene `graph.sqlite3`: nodi per le capability pubblicate, alias e archi
tipizzati con file, riga, testo del riferimento e provenienza. Come in `C:/git/Teseo`,
il grafo è derivato dai documenti: frontmatter, wikilink e sezioni Related delle skill.
I riferimenti assenti o ambigui sono segnalati, senza inventare nodi. Blocchi di codice
e identificatori mostrati come esempi non producono collegamenti.

La ricerca combina BM25 e vicini del grafo: al massimo tre passi in avanti e due
indietro, senza cambiare direzione lungo un percorso; i collegamenti deboli valgono
solo al primo passo. Una penalità riduce il peso dei nodi molto collegati. Le schede
recuperate dal grafo includono il percorso in `retrieval`. Filtri, quarantena,
limiti di risposta e controlli del piano continuano ad applicarsi a ogni risultato.
I riferimenti nei manuali sono suggerimenti di ricerca, non dipendenze da caricare.

Ogni pubblicazione costruisce il proprio grafo nella stessa transazione del catalogo;
il rollback seleziona il grafo della versione precedente. Per uno stato registry già
esistente, eseguire una volta il rebuild esplicito (in sua assenza resta la ricerca lessicale):

```powershell
& $python @capabilityArgs graph --rebuild
& $python @capabilityArgs graph --limit 20 --offset 0
& $python @capabilityArgs graph --capability-id <id>
```

`registry_diagnostics` espone prontezza e conteggi del grafo; il server conserva dieci
tool MCP. Il rebuild e l'ispezione dettagliata sono comandi CLI dell'operatore.

### Migrazione esplicita da v0.1

La migrazione non viene eseguita dall'avvio del registry. Prima prepara una directory sorella
e ne blocca le emissioni fino al cutover esplicito. Non esiste una migrazione automatica di
una `.fsr` reale.

```powershell
$legacy = "$PWD/.fsr"
$v2 = "$PWD/.fsr-v2"

# Importazione offline: verifica schema, radice, integrità e snapshot; lascia v0.1 utilizzabile.
& $python -m fastskillretrieval --source-root $root migrate-state $legacy $v2

# Dopo aver fermato gli utilizzatori v0.1 e controllato la diagnostica del nuovo stato:
& $python -m fastskillretrieval --source-root $root migrate-state $legacy $v2 --activate
```

`--activate` ricontrolla che il legacy non sia cambiato, protegge il backup e completa il
cutover. Solo allora il vecchio percorso viene sostituito da un tombstone che impedisce a un
runner v0.1 di riaprirlo; il percorso ritirato non torna utilizzabile. Un'interruzione o una
radice/schema/snapshot non validi non attivano il nuovo registry. Conservare il backup e
verificare il piano operativo prima di questa azione esplicita.

### Stato delle prove v0.3

Il benchmark di ingestion ha usato 5.000 capability, sorgenti e pubblicazione reali. Il batch
iniziale da 100 ha rispettato la deadline restituendo timeout; la ripresa esplicita con batch da
10 ha completato il catalogo. Su 100 ricerche indipendenti (dieci warm-up, limite 1) la ricerca
ha misurato p50 **32,72 ms**, p95 **64,91 ms** e massimo **124,20 ms**, senza LLM. È una misura
di fixture sintetica, non dimostra la qualità del recupero, la latenza del client o una riduzione
del contesto del 30%.

La suite completa ha superato **198 test**, con **uno skip** e **86% di copertura**. Ruff, i due
smoke MCP sorgente e i due smoke MCP dalla wheel isolata passano. Il report completo, inclusi
gate completati e limiti, è [doc/Capability_Implementazione.md](Capability_Implementazione.md)
e l'evidenza sintetica corrente è [doc/Capability_Verifica_v0.3.json](Capability_Verifica_v0.3.json).

`reconcile --cursor` riprende batch di manutenzione e può produrre finding da revisionare,
senza archiviare o disabilitare capability. L'`export` paginato minimizza i dati a identità,
tipo/stato, ammissione, contatori aggregati e finding: il server non osserva l'esposizione
eleggibile né deduce utilità. La retention di eventi, piani e metadati logici ha un tetto di
100 MB UTF-8, che non è un limite fisico del database o degli snapshot. Esempio:

```powershell
& $python @capabilityArgs reconcile --cursor 0
& $python @capabilityArgs export --offset 0 --limit 10 --kind skill --status enabled
```

## Verifiche e pacchetto

```powershell
& $python -m pytest -q --basetemp=.work/pytest
& $python scripts/verify_mcp.py
& $python scripts/verify_registry_mcp.py
& $python -m ruff check src tests scripts
& $python -m coverage erase
$env:FSR_COVERAGE = '1'
& $python -m coverage run --parallel-mode -m pytest -q --basetemp=.work/pytest-coverage
& $python scripts/verify_mcp.py
& $python scripts/verify_registry_mcp.py
& $python -m coverage combine
& $python -m coverage report
Remove-Item Env:FSR_COVERAGE
uv build --out-dir dist
```

Lo smoke test avvia davvero il processo server, negozia MCP e chiama i tool tramite il client
SDK su un catalogo temporaneo. Non chiama Claude e non richiede quota LLM. La wheel è locale,
non pubblicata su PyPI. I risultati effettivi e le limitazioni sono registrati in
[doc/Capability_Implementazione.md](Capability_Implementazione.md); la baseline storica è
[doc/Prima_Implementazione.md](Prima_Implementazione.md).

Per collaudare una wheel già installata in un ambiente separato, eseguire
`scripts/verify_mcp.py --installed` e `scripts/verify_registry_mcp.py --installed` con il
Python di quell'ambiente. Entrambe le opzioni rifiutano un'installazione editable dai sorgenti,
così il percorso del pacchetto non viene confuso con quello del repository.

Restano condizionali: attestazione e attivazione native Claude, dispatch end-to-end del planner,
catalogo gestito, riduzione di contesto del 30% e lettura dei risultati della prima misurazione
nel corpus umano di 100 skill/20 richieste (`doc/Capability_Eval_100_20_Proposta.md`). La scala
sintetica a 5.000 è un'indicazione di ricerca del registry, non un risultato di utilità o di integrazione host.

Il [piano di completamento ECC del 22 ottobre](../plans/FastSkillRetrieval_Completamento_ECC.md)
ricostruisce il lavoro residuo in 12 incrementi con gate di verifica e dipendenze.
Il [piano Capability Retrieval](../plans/FastSkillRetrieval_Capability_Retrieval.md) analizza
l'estensione a subagent, workflow e altri tipi di capacità, con migrazione, contratti e
criteri di verifica. La sua sezione finale registra l'avanzamento e i gate ancora aperti.
