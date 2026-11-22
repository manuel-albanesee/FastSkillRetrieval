# Capability Retrieval — implementazione v0.3

Data: 18 ottobre 2026. Stato: implementazione locale verificata ai gate sotto indicati;
integrazione nativa del planner ancora condizionale. Questo documento separa ciò che il
registry ha effettivamente fatto da ciò che richiede una ricevuta del runtime host o una
valutazione umana. Consegna coordinata con ECC TDD/Orch e subagent GPT-5.6 Terra high.

**Verifica finale:** 155 test superati, uno saltato, copertura delle istruzioni Python
**87%** (1.751/2.008). Ruff senza errori; smoke SDK legacy e v2 riusciti dal sorgente
e dalla wheel 0.2.0 installata in un ambiente separato. La revisione finale indipendente
non ha trovato ulteriori difetti HIGH/CRITICAL concreti.
L'ultimo controllo ha aggiunto due regressioni sull'apertura incrociata degli stati:
v2 rifiuta una directory v1 e il nuovo ingresso legacy rifiuta una directory v2,
prima di creare qualunque database parallelo. La migrazione in directory sorella resta esplicita.

## Perimetro e attivazione

Il registry v2 è deliberatamente separato dalla baseline v0.1. Il flag CLI `--registry`
seleziona `Registry`; senza flag continuano `Catalog` e i contratti legacy. Non vi è upgrade
automatico di `.fsr`, né una `.fsr` reale è stata migrata durante queste prove.

| Superficie | Implementato e verificato localmente | Limite esplicito |
|---|---|---|
| Discovery e descrittori | Discovery esplicita delle capability autorizzate, revisioni/snapshot immutabili, alias, stati e pubblicazione atomica | I contenuti restano non attendibili; un descrittore conforme non è una certificazione semantica o di sicurezza |
| Ricerca e piano | Ricerca per tipo, riferimento versionato, piano di dipendenze e `prepare` immediatamente prima dell'uso | Nessuna API di piano esegue azioni; le azioni native senza attestazione restano bloccate in dry run |
| Skill semplice | `load`/`resource` verificano il pacchetto e possono emettere testo assistito con `dry_run=false` | L'emissione non dimostra applicazione delle istruzioni da parte del modello |
| Subagent e workflow | Sono discoverable e pianificabili con contratti distinti | Nessuna delega o workflow viene eseguito dal registry |
| Runtime Claude | L'adapter riporta configurazione e stato diagnostico | La lettura assistita semplice può essere consentita; senza attestazione host l'attivazione nativa resta `False` e fail-closed |

I comandi principali, tutti espliciti, sono `discover`, `inspect`, `submit`, `publish`,
`search`, `plan`, `prepare`, `reconcile`, `rollback`, `set-status`, `migrate-state`,
`export`, `diagnostics` e `serve`. `search --mode discovery|prepare_use` e
`plan --mode discovery|prepare_use` espongono le due modalità preparatorie. Il server MCP
v2 mantiene gli otto tool legacy e aggiunge nove tool v2: **17 tool** complessivi.

```powershell
$python = (Resolve-Path .venv/Scripts/python.exe).Path
$root = (Resolve-Path examples/skills).Path
$state = "$PWD/.fsr-v2"
$common = @('-m', 'fastskillretrieval', '--registry', '--source-root', $root,
            '--state', $state, '--session', 'operator')

& $python @common discover
& $python @common search 'controllare Markdown' --kind skill --mode discovery
& $python @common diagnostics
```

Per creare un piano, usare il riferimento JSON emesso da `search`:

```powershell
& $python @common plan '<reference JSON>' --mode prepare_use
& $python @common prepare '<plan_id>'
```

## Stato, manutenzione ed export

`reconcile` è un'operazione esplicita e riprendibile tramite `--cursor`: classifica fonti
bloccate o rimosse, alias irrisolti e overlap di descrittore identico. Dopo 20 risposte di
ricerca dalla prima pubblicazione può produrre il finding `low_retrieval_review_only`;
questo richiede revisione e non archivia o disabilita automaticamente una capability.
L'ammissione indica l'inizio del tracciamento v2, senza inventare la data storica delle skill
importate. Le finestre aggregate usano giorni UTC; i denominatori non provano eleggibilità.

`export` è paginato e minimizzato. Espone solo ID, tipo, stato, `admitted_at`,
`search_responses_since_admission`, `retrieved`, finestre 7/30/90 giorni e findings. Il
denominatore dichiara `server_searches_not_observed_or_eligible_exposure`: il server non
deduce esposizione eleggibile o utilità. Non vengono esportati testo delle query o contenuti.

```powershell
& $python @common reconcile --cursor 0
& $python @common export --offset 0 --limit 10 --kind skill --status enabled
```

La retention include il ledger di eventi, piani e metadati logici UTF-8 fino a 100 MB. È un
limite logico dei dati tracciati, non una quota fisica del database, degli snapshot o dei file.
Lo schema 2 è soggetto a validazione strutturale e non viene aggiornato automaticamente.

## Migrazione v0.1 → v2

La migrazione è offline e a due fasi. Il primo comando prepara il target sibling, controllando
schema legacy, radice autorizzata, WAL/integrità, budget, tentativi, snapshot e riferimenti.
Il registry preparato resta `pending_activation` e rifiuta emissioni; il catalogo v0.1 rimane
operativo. L'operatore ferma i runner legacy, controlla il target e richiede poi l'attivazione.

```powershell
$legacy = "$PWD/.fsr"
$target = "$PWD/.fsr-v2"

& $python -m fastskillretrieval --source-root $root migrate-state $legacy $target
& $python -m fastskillretrieval --registry --source-root $root --state $target diagnostics
& $python -m fastskillretrieval --source-root $root migrate-state $legacy $target --activate
```

L'attivazione confronta l'impronta legacy acquisita nella preparazione, protegge il backup e
gestisce il cutover in modo riprendibile. Al successo, sostituisce il percorso legacy con un
tombstone e ritira il vecchio stato per impedire che un runner v0.1 continui a scriverlo.
Non è automatico: mancano `--activate`, runner fermati, backup proteggibile o impronta
coerente significano nessun cutover. Un guasto durante la preparazione lascia il legacy
invariato; uno stato intermedio di rinomina viene riconosciuto e completato o rifiutato senza
riaprire il vecchio catalogo.

## Evidenza dei runtime

La verifica SDK ha effettuato **13 chiamate MCP reali** nella superficie v2. La verifica host
separata su Claude Code CLI 2.1.302 ha effettuato **sei chiamate legacy MCP reali**:
diagnostica, due ricerche, load, resource consentita e resource traversal rifiutata. Ha inoltre
osservato una delega nativa minima `Agent` con hand-back e zero tool use. La matrice, le
ricevute e le limitazioni sono in [Capability_Host_Verification.md](Capability_Host_Verification.md).

Queste prove completano il trasporto, gli ingressi e l'assistenza lettura. Restano
**condizionali**: attestazione host per attivazione nativa, dispatch end-to-end registry →
planner → runtime, preload skill del subagent, hook/policy/catalogo gestito e prova che
`tools: []` sia effettivamente enforced. La rilevazione di Claude Code 2.1.302 è una
superficie osservata, non una certificazione di compatibilità generale.

## Benchmark sintetico

I run iniziali sono in `.work/host-proof/benchmark-100.json` e
`.work/benchmark-5000-final.json`. La ripetizione sul codice finale è in
`.work/benchmark-final-remeasurement.json`; include campioni e hash dei moduli.
I risultati finali del 18 ottobre 2026 sono:

| Corpus/scope | Campionamento | Risultato |
|---|---|---|
| 100 capability sintetiche, seed SQL validato, file e snapshot reali | 10 warm-up, 100 campioni, 20 query × 5, `limit=1` | 100/100 `ok`; p50 **31,9728 ms**, p95 **36,0048 ms**, max **44,4383 ms** |
| 5.000 capability sintetiche, seed SQL validato, file e snapshot reali | 10 warm-up, 100 campioni, 20 query × 5, ordine seed 20261018, `limit=1` | 100/100 `ok` |
| `Registry.call(search)` + serializzazione JSON, 5.000 capability | sessione indipendente per campione; nessuna chiamata LLM | p50 **31,2075 ms**, p95 **35,5995 ms**, max **49,9772 ms** |
| Preparazione iniziale delle fixture riutilizzate | 13,694 s / 345,200 s | esclusa dalle latenze di ricerca |

Il benchmark conserva controlli pubblici d'integrità/budget e verifica il marker di risorsa e
il rifiuto dello snapshot manomesso. Non misura il client MCP/Claude, qualità semantica,
fatturazione, il corpus umano 100 skill/20 richieste o la riduzione del contesto del 30%.
La ripetizione usa il codice finale, compresi kind immutabile, alias fail-closed e fingerprint
di catalogo/configurazione. Sono stati aggiunti alle sole fixture sintetiche i nuovi aggregati
prima del controllo strutturale; pacchetti e descrittori rimangono quelli validati inizialmente.
Non è una misura della pipeline completa di ingestion/pubblicazione di 5.000 sorgenti.
I risultati sotto 300 ms valgono per questo carico artificiale, con un solo match per query;
non certificano distribuzioni di query o pacchetti diversi.

## Gate di verifica

| Gate | Stato | Evidenza/condizione |
|---|---|---|
| Contratti registry e runtime | Completo localmente | Coprono discovery, piani, budget di gruppo, candidata corrente, migrazione, fault injection e retry limitati per rinomina Windows |
| Migrazione | Completo localmente | Prepare separato, attivazione esplicita, fingerprint, backup/tombstone, WAL e recupero da interruzione |
| Correzioni mirate | Completo localmente | Il budget rifiuta anche il gruppo esistente/genitore tardivo da 4.828 byte oltre il limite di 4.000 tramite `content_charges`; migration candidate current e quattro retry limitati di rename Windows sono coperti |
| SDK MCP | Completo localmente | 13 chiamate reali sul server v2 |
| Claude MCP e delega minima | Completo per gli scenari osservati | Sei chiamate legacy e una delega `Agent`, senza generalizzare alla pianificazione |
| Attivazione nativa e planner dispatch | Condizionale | Richiede attestazione host e prova end-to-end positiva |
| Utilità e riduzione contesto | Condizionale | Richiede 100 skill/20 richieste reali concordate; l'obiettivo del 30% non è dimostrato |
| Scala sintetica della ricerca | Completo per il carico dichiarato | 100 e 5.000 elementi, 100/100 risposte corrette ciascuno; p95 sotto 300 ms, senza trasferire il risultato al corpus umano |
| Wheel e sorgenti | Completo localmente | Build offline, dipendenze da lock, SDK legacy/v2 dalla wheel installata; nessuna pubblicazione remota |

La suite completa ha riportato **155 passed, 1 skipped in 94,15 s**, con copertura estesa
ai subprocess CLI/MCP tramite `FSR_COVERAGE=1`. Il solo skip riguarda la creazione di symlink
Windows, non consentita dai privilegi correnti; le altre prove sui percorsi rimangono verdi.
Dopo l'allineamento finale dello schema JSON `task_revision`, i suoi dieci test sono stati
rieseguiti e passano. Il report di copertura è `.work/coverage-final-v2.json`.

Il launcher `.exe` della nuova wheel è stato riprovato e restituisce ancora **Accesso negato**.
Il percorso verificato è `python -m fastskillretrieval`, anche nel processo server MCP.
Gli smoke emettono un warning della dipendenza `pydantic-settings` sulla forward reference
`lifespan`, senza errori nelle negoziazioni/chiamate osservate. I tentativi SDK nella sandbox
che non hanno completato il trasporto non sono conteggiati come successi.

Per rieseguire le verifiche:

```powershell
$python = (Resolve-Path .venv/Scripts/python.exe).Path
& $python -m pytest -q --basetemp=.work/pytest
& $python scripts/verify_registry_mcp.py
& $python -m ruff check src tests scripts
& $python scripts/benchmark_registry.py --size 100
& $python scripts/benchmark_registry.py --size 5000 --state .work/benchmark-5000-repeat
```

L'ultimo comando richiede una directory nuova o vuota e produce soltanto un fixture
disposable. Il suo tempo di preparazione non va confrontato con la latenza di ricerca.

## Decisioni e limiti di consegna

- P1–P6 e P8 sono implementati nel perimetro locale; P7 resta chiuso per l'integrazione nativa.
  La prova host singola non soddisfa il protocollo di tre ripetizioni del piano.
- Il confronto umano A/B/C, i richiami essenziali 100/20 e il 30% richiedono il corpus
  concordato; `examples/capability-eval-template.json` resta non approvato e vuoto.
- Discovery prepara i file di un batch prima di acquisire il writer, con cursore legato alla
  lista di directory. Non certifica un'istantanea simultanea di tutte le sorgenti; ciascun
  pacchetto viene rivalidato alla pubblicazione e all'uso. Le operazioni hanno deadline finite.
- Due connessioni concorrenti non possono emettere oltre il budget condiviso. SQLite può
  rifiutare l'upgrade del lock con `catalog_unavailable`; il retry è esplicito e non azzera consumi.
- Lo stato operativo, la chiave HMAC e le fixture di lavoro sono esclusi da Git e dalle
  distribuzioni. Il repository resta locale su `main`, senza origin, commit o pubblicazioni.
- Gli schemi JSON documentano gli ingressi; i controlli semantici eseguibili restano nei
  validator Python. Riferimenti, scope, file, grafo e autorità vengono verificati dal codice.

## Aggiornamento di chiusura v0.3

La verifica locale aggiornata ha concluso con **198 passed, 1 skipped** e copertura del
**86%**. Il parser capability limita gli identificatori, l'approvazione della valutazione lega
corpus e review a due impronte e il bridge nativo rifiuta preload con risorse non
materializzabili. Non esiste un invocatore automatico del bridge dal registry o da MCP.

Il benchmark completo di ingestion a 5.000 elementi ha usato `discover`, `submit` e `publish`.
Il batch iniziale ha raggiunto il timeout controllato; una ripresa esplicita con batch da 10 ha
pubblicato tutti gli elementi. La ricerca ha prodotto p50 32,72 ms e p95 64,91 ms su 100
campioni. I comandi riproducibili sono:

```powershell
$python = (Resolve-Path .venv/Scripts/python.exe).Path
& $python -m pytest -q --basetemp=.work/pytest
& $python scripts/verify_mcp.py
& $python scripts/verify_registry_mcp.py
& $python scripts/benchmark_ingestion.py --size 5000 --state .work/ingestion-5000
# Solo dopo un'interruzione osservata della stessa fixture:
& $python scripts/benchmark_ingestion.py --size 5000 --state .work/ingestion-5000 --resume
```

La proposta 100/20 è pronta, ma non approvata: nessun confronto A/B/C né obiettivo di riduzione
del 30% viene dichiarato raggiunto.
