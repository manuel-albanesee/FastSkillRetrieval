# Percorso operatore del completamento ECC

22 ottobre 2026. Windows, Python 3.13. Le interfacce operatore non vengono importate
dal registry e non ne cambiano la retention. Il core continua a cercare e consegnare
manuali senza eseguire modelli. La CLI sperimentale può avviare Claude soltanto con
il sottocomando esplicito `run` o `resume` e un protocollo autorizzato.

## Preparazione e uso del registry

L'operatore prepara una nuova radice di sorgenti e un nuovo stato, acquisisce le
revisioni con `discover`, ispeziona i manuali, propone i descrittori con `submit` e
pubblica il catalogo con `publish`. Il client finale usa il catalogo pubblicato:
`search` restituisce riferimenti completi; `plan` controlla dipendenze e conflitti;
`prepare` verifica il piano; `load` consegna i manuali completi; `resource` legge
solo le risorse del pacchetto autorizzato. La consegna non certifica applicazione.

I comandi e il template MCP già documentati nel README restano l'ingresso del core.
Usare `python -m fastskillretrieval --registry --source-root <radice> --state <stato>`;
aggiungere `--session <id>` per le operazioni della stessa sessione. La superficie MCP
espone dieci tool. Preparazione e manutenzione sono compiti dell'operatore, distinti
dalla sessione valutata; i profili sperimentali negano i tool di pubblicazione e ingestione.

## Validare e pianificare senza modello

Dal repository originale `C:/git/FastSkillRetrieval`, PowerShell:

```powershell
$python = (Resolve-Path .venv/Scripts/python.exe).Path
& $python -m fastskillretrieval.experiment_cli validate --protocol examples/experiments/completion-v1.json
& $python -m fastskillretrieval.experiment_cli plan --protocol examples/experiments/pilot-v1.json --output .work/my-new-plan
& $python -m fastskillretrieval.experiment_cli report --run .work/my-new-plan --output .work/my-new-report.json
```

`plan` richiede una directory nuova; congela protocollo, schedule e hash del codice.
Ogni case/repetition/braccio ha un ID distinto; l'ordine dei bracci viene alternato.
Il report di un piano non eseguito resta inconclusive, con costi e riduzioni null.
I template pubblici contengono pin del client da sostituire, budget null e nessuna
approvazione. Sono proposte da revisionare, non input già pronti per una prova definitiva.
La proposta principale contiene development e holdout per la revisione: prima di un run
estrarre un solo split in una nuova revisione. L'esecuzione rifiuta split misti o diversi
dal purpose congelato, per impedire di includere il holdout nel tuning development.

`examples/experiments/config-v1.json` documenta le scelte del dataset; il suo hash nei
template ne conserva la revisione. **Non è una configurazione runtime di Claude.**
Per un run futuro costruire un oggetto usando la funzione `execution_config` del
modulo `experiment_cli`: MCP esplicito, descrittori completi e istruzioni versionate.
Usare il risultato di `fingerprint(config)` come nuovo `config_sha256` nel protocollo;
incrementare revisione e congelare nuovamente il protocollo prima dell'approvazione.
In B ogni descrittore deve includere l'ID `ecc/nome` e il riferimento opaco completo
restituito dal registry. Il catalogo B viene inserito letteralmente nel prompt:
la sua sola presenza sul disco non è una misura host.

## Profili client e futura esecuzione esplicita

A_NATIVE materializza i manuali hash-pinned in `.claude/skills` nella cwd nuova,
usa fonte `project`, abilita `Skill` e non connette FSR. B_CURATED nasconde il
catalogo nativo, passa il catalogo curato completo nel prompt e permette solo
load/resource/diagnostics. C_FSR nasconde le skill native e permette i tool operativi
di ricerca, piano, prepare, load, resource e diagnostica. Entrambi i profili B/C
verificano i dieci tool connessi, negando esplicitamente quelli di manutenzione.

Il preflight è un comando separato con un endpoint provider non raggiungibile,
autenticazione fittizia e retry disabilitati. Verifica init, versione, tool,
contaminazioni e contatori di uso zero; un errore o un formato client non riconosciuto
lascia il gate chiuso. Non vengono applicate queste credenziali ai run reali.
Nella consegna di questa sessione, per scelta dell'utente, non è stato avviato Claude.

```powershell
& $python -m fastskillretrieval.experiment_cli preflight --protocol <protocol.json> --source-root <corpus-root> --config <runtime-config.json> --output <new-probe-dir> --local-only
& $python -m fastskillretrieval.experiment_cli run --protocol <approved.json> --source-root <corpus-root> --config <runtime-config.json> --output <new-run-dir>
& $python -m fastskillretrieval.experiment_cli resume --run <run-dir> --source-root <corpus-root> --config <runtime-config.json>
& $python -m fastskillretrieval.experiment_cli resume --run <run-dir> --source-root <corpus-root> --config <runtime-config.json> --retry <failed-trial-id>
```

Servono nomi e date delle revisioni umane per ciascun caso, autorizzazione con
fingerprint, modello/versione effettivi, autenticazione e budget espliciti:
`trial_usd`, `session_usd`, `experiment_usd`, `max_turns`, `timeout_seconds`,
`inflight_overshoot_usd`. I tetti sessione/esperimento sono cumulativi sullo stesso
run, anche dopo resume: non esiste un reset implicito. Importi finiti non negativi,
con trial <= sessione <= esperimento. Per ogni tentativo viene riservato trial più
overshoot prima dello spawn. Un tentativo a costo sconosciuto conserva la riserva.
Il limite CLI e la terminazione del processo non provano il costo finale delle
richieste già in volo. Se il budget residuo non basta, la coda si ferma.

Il runner usa argv strutturati senza shell. Richiede un eseguibile nativo anziché
wrapper `.cmd`, `.bat` o `.ps1`. Il modello effettivo e la versione osservati in init
devono coincidere coi pin. Nessun checker o codice candidato viene eseguito dal
runner. I task supportati sono dichiarativi: i profili non espongono tool filesystem
o shell; la cwd da sola non viene presentata come sandbox attestata.

## Ricevute, output e revisione

Il database SQLite dei tentativi è autorevole; `journal.jsonl` è una traccia durevole.
Crash dopo riserva/prima spawn produce `unknown` alla ripresa e non autorizza retry
automatici. I tentativi falliti e i loro costi rimangono nel report; il costo totale
resta null finché mancano trial o costi, con subtotal e riserve riportati separatamente.

Ogni tentativo conserva argv con payload oscurato, preflight, receipt correlate,
osservazione tipizzata e `task-output.json` per la revisione. I tool_use sono accettati
solo dal canale assistant e i tool_result dal canale user; il body deve corrispondere
al corpus e il riferimento alla chiamata. Un elenco di candidati non è una scelta,
una scelta non è un caricamento, un caricamento non è applicazione. La risposta
nativa «Launching skill» lascia il caricamento sconosciuto senza body verificabile.

Per una revisione esplicita usare `review --run <dir> --review-file <json>`.
Il JSON richiede `trial_id`, `attempt`, `reviewer`, `reviewed_at`,
`observation_sha256` (fingerprint canonico dell'osservazione), `output_sha256`
(SHA-256 dei byte di task-output.json), `task_success`, `applied_skill_ids` ed
`evidence`. La revisione è legata a questi artefatti immutabili e non può inventare
manuali caricati; il tool registra l'identità dichiarata dell'operatore senza
autenticarla. Nessuna revisione umana viene generata automaticamente dall'agente.

Uso del modello, cache, costi, byte FSR e catalogo host sono campi diversi. Il rapporto
host usa `1 - media(C)/media(B)` o il corrispondente A/C solo su coppie interamente
osservabili e omogenee. Byte server, stime arrotondate e somma degli input runtime
non certificano la riduzione. Misure mancanti e denominatori zero producono null.
La generalizzazione resta non dimostrata: il software non inventa un'analisi di potenza.

## Installazione isolata, aggiornamento e recupero

La build offline usa il backend disponibile nella cache di progetto:

```powershell
uv build --offline --cache-dir .work/uv-cache --out-dir .work/my-new-release/dist
uv venv --offline --python .venv/Scripts/python.exe .work/my-new-release/venv
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file .work/my-new-release/runtime.lock.txt
uv pip sync --offline --require-hashes --cache-dir .work/uv-cache --python .work/my-new-release/venv/Scripts/python.exe .work/my-new-release/runtime.lock.txt
uv pip install --offline --no-deps --python .work/my-new-release/venv/Scripts/python.exe .work/my-new-release/dist/fastskillretrieval-0.3.0-py3-none-any.whl
```

Avviare il Python di quell'ambiente da una cwd esterna a `src`, senza PYTHONPATH,
e controllare il percorso `fastskillretrieval.__file__`. Il launcher verificato su
Windows resta `python -m`, compreso `python -m fastskillretrieval.experiment_cli`.
Un ambiente offline senza gli artefatti necessari fallisce esplicitamente: non scarica
codice o cambia installazioni globali come fallback.

Per aggiornare sorgenti operatore: conservare il manifest e i file precedenti;
eseguire reconcile e controllare finding/stati; pubblicare solo descrittori validati;
usare i nuovi riferimenti. Quarantene e consumi non vengono azzerati. Il rollback
del registry usa il catalogo precedente ma conserva disabilitazioni/quarantene e ledger.
Il recupero software usa i soli file enumerati nella ricevuta di sincronizzazione e
i backup della baseline; non ripristina un database vecchio sopra quello in uso.

Il bundle locale di questa consegna è
`.work/completion-implementation-20261022` nel repository originale; include baseline,
backup dei soli file sostituiti, ricevute, lock runtime e build. Le prove assistite
sono in `.work/completion-session-flat-20261022`. Prima di distribuire il bundle
servono redazione e revisione umana; sdist/wheel escludono `.work`, `.venv`, `.git`
e lo stato personale. Nessuna configurazione globale del client è stata installata.

La pubblicazione comprende batch di snapshot e due fasi del grafo (nodi/alias, poi
archi). I cursori sono durevoli; le strutture provvisorie del grafo non sono esposte
alle ricerche. Un `in_progress` richiede una successiva chiamata operatore. Il parametro
`publish --batch-size` limita gli snapshot; il lavoro del grafo è limitato separatamente
da `Limits.batch`, con massimo 100 unità per chiamata. Il vecchio catalogo/grafo rimane
attivo fino allo scambio atomico finale, anche durante un rebuild ripreso.
