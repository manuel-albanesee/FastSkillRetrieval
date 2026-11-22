# Proposta di valutazione umana 100 skill / 20 richieste

**Stato: proposta revisionata e approvata (2026-10-20); prima misurazione eseguita.** La
revisione umana dei 20 richiami essenziali è registrata in `examples/capability-eval-proposal.json`
(che resta `proposed`, come prevede lo script); la copia con approvazione operatore è in
`.work/proposal-approved.json`, che non è versionata. Impronta di revisione:
`00275b967f265f66599b6866e2aa7d4acfeac4c31853e8784ff2cbb6e71bb2bb`. I risultati sono nella
sezione «Prima misurazione».

## Corpus proposto

La fixture contiene 100 file di definizione `SKILL.md` ECC realmente presenti nel bundle locale ECC 2.2.3.
Ogni riferimento registra il percorso relativo al bundle e lo SHA-256 del file. La preparazione
deve copiare soltanto quei file nella radice di lavoro autorizzata e ricontrollare gli hash:
non modifica il bundle ECC né importa altre directory personali. Non dichiara di aver copiato
pacchetti completi, né che tutte le definizioni siano invocabili dal client nativo.

I 100 elementi coprono in modo intenzionale agenti, ricerca/eval, API e backend, frontend,
test, sicurezza, pianificazione e linguaggi. Non sono una rappresentazione statistica del
catalogo ECC; sono il campione concreto da sottoporre alla revisione umana.

## Richieste e revisione umana richiesta

Le 20 richieste sono in italiano e hanno soltanto `proposed_essential_capability_references`.
Sono suggerimenti tracciabili dal nome e dal frontmatter del file, non giudizi di rilevanza.
Prima dell'esecuzione un revisore deve, per ciascuna richiesta:

1. sostituire i suggerimenti con `expected_essential_capability_references`, oppure registrarne
   esplicitamente l'assenza;
2. indicare identità, data e nota del revisore;
3. far registrare a un operatore la decisione esplicita e congelare proposta e hash del corpus.

L'impronta lega la decisione al contenuto revisionato, ma non è una firma crittografica né prova
dell'identità del revisore.

Finché questi campi restano `null`, qualsiasi calcolo di recall essenziale è `null`, non zero e
non positivo.

| ID | Richiesta | Riferimenti essenziali proposti, da confermare o correggere |
|---|---|---|
| q01 | Progetta una API REST paginata e sicura per ordini. | `api-design`, `security-review`, `contract-first` |
| q02 | Aggiungi un endpoint FastAPI con validazione e gestione degli errori. | `fastapi-patterns`, `python-patterns`, `error-handling` |
| q03 | Individua e risolvi un rallentamento di rendering in una pagina React. | `react-performance`, `react-patterns`, `benchmark-optimization-loop` |
| q04 | Scrivi test accessibili per un componente React con una chiamata HTTP simulata. | `react-testing`, `frontend-a11y`, `accessibility` |
| q05 | Aggiungi una regressione Python per un bug trovato da codice generato da AI. | `python-testing`, `ai-regression-testing`, `tdd-workflow` |
| q06 | Pianifica una migrazione PostgreSQL reversibile senza perdita di dati. | `database-migrations`, `postgres-patterns`, `safety-guard` |
| q07 | Rivedi le dipendenze di un server MCP prima del rilascio. | `mcp-dependency-review`, `mcp-server-patterns`, `security-review` |
| q08 | Costruisci una valutazione ripetibile per confrontare due agenti di coding. | `agent-eval`, `eval-harness`, `benchmark` |
| q09 | Diagnostica un agente che fallisce in modo intermittente e produci evidenze. | `agent-introspection-debugging`, `agent-architecture-audit`, `agent-self-evaluation` |
| q10 | Metti in sicurezza un runtime di agenti che elabora contenuti non fidati. | `agent-security-hardening`, `security-review`, `safety-guard` |
| q11 | Progetta un sistema multi-agente persistente con memoria e automazioni. | `agentic-os`, `agent-harness-construction`, `agentic-engineering` |
| q12 | Riduci la latenza p95 di un servizio con una prova prima e dopo. | `latency-critical-systems`, `benchmark-optimization-loop`, `benchmark` |
| q13 | Crea una guida di onboarding con i punti di ingresso e un tour del codice. | `codebase-onboarding`, `code-tour`, `documentation-lookup` |
| q14 | Registra una decisione architetturale dopo avere analizzato l'impatto della modifica. | `architecture-decision-records`, `change-impact`, `living-docs-governance` |
| q15 | Verifica in browser una funzionalità distribuita su tre breakpoint e controlla l'accessibilità. | `browser-qa`, `e2e-testing`, `accessibility` |
| q16 | Trasforma un obiettivo di prodotto in un piano di implementazione con dipendenze. | `blueprint`, `plan-orchestrate`, `product-capability` |
| q17 | Rivedi la qualità di una pull request e prepara un messaggio Conventional Commit. | `codehealth-mcp`, `conventional-commit-review`, `coding-standards` |
| q18 | Ricerca fonti aggiornate per una decisione tecnica e sintetizza le evidenze. | `deep-research`, `research-ops`, `documentation-lookup` |
| q19 | Trova una skill esistente prima di svilupparne una nuova. | `skill-scout`, `search-first`, `agent-sort` |
| q20 | Controlla se le skill configurate sono effettivamente rispettate dall'agente. | `skill-comply`, `skill-stocktake`, `skill-host-compat` |

## Confronto A/B/C

| Braccio | Cosa va osservato | Regola di interpretazione |
|---|---|---|
| A | Catalogo nativo esposto dal client e ricevuta/osservazione di contesto | Se il client non lo espone, i campi A restano `null`. |
| B | Catalogo curato senza retrieval, usando gli stessi descrittori curati di C | Il catalogo deve essere materializzato e la sua impronta registrata. |
| C | Gli stessi descrittori di B, con retrieval FastSkillRetrieval | Registrare risultati e impronta del catalogo; nessun giudice semantico. |

Il runner calcola esclusivamente confronti di insiemi contro gli essenziali approvati e conteggi
di byte di catalogo quando esiste evidenza osservabile. Non interpreta il testo delle risposte,
non chiama modelli e non deduce A dal catalogo B o C. La riduzione del 30% è dimostrabile solo
con valori A e C osservati; altrimenti resta `null`. I tempi preparazione, catalogo, nuove
istruzioni, runtime main e runtime delegated sono colonne separate, senza sommarle. Il runtime
main osservato comprende anche header e altro contesto client; la colonna catalogo non pretende
di essere il costo totale del runtime.

## Invarianti del runner proposto

- richiede esattamente 100 skill e 20 richieste con ID univoci;
- rifà SHA-256 dei file fissati e rifiuta modifiche, casi post-hoc e risultati riferiti a un
  fingerprint diverso;
- accetta una firma di approvazione dell'operatore soltanto per una proposta completa di
  recensioni umane; non esiste un valore `approved` implicito;
- mantiene `null` per misure non osservabili e separa runtime, costi e ricevute main/delegated;
- scrive soltanto nella directory output esplicitamente scelta e non salva query fuori dal
  dataset di valutazione autorizzato.

## Prima misurazione (2026-10-20)

Fixture: 100 `SKILL.md` copiati dal bundle ECC 2.2.3, 0 hash diversi, registro pubblicato con
100 capability (`catalog_id` 1). Client Claude Code 2.1.304. I file sono in `.work/`
(`real-eval-fixture-4`, `real-eval-results-4.json`, `real-eval-measure-4.json`,
`real-eval-c-detail-4.json`) e non sono versionati.

| Braccio | Richiamo essenziale | Richieste con tutti gli essenziali | Byte di catalogo (somma su 20 richieste) |
|---|---|---|---|
| A | `null` (non osservabile) | `null` | `null` |
| B | 38/38, per costruzione | 20/20 | 702.480 (35.124 × 20) |
| C | 6/38 (15,8%) | 1/20 | 16.723 |

- **A non è osservato.** `/context` di Claude Code 2.1.304 non riporta una categoria per le skill,
  quindi il catalogo nativo non è misurabile. Il file dei risultati contiene selezioni vuote per A
  solo perché lo schema richiede una lista; il richiamo 0,0 che `measure` stampa per A non è una
  misura. `context_reduction_A_to_C` resta `null`: la riduzione del 30% non è dimostrata.
- **B vale 100% per definizione**, perché senza retrieval tutti i descrittori sono visibili. Non
  misura se l'agente scelga bene tra 100 skill.
- **C è la sola misura di retrieval.** Il registro restituisce quasi sempre 1 candidato
  (`omitted_for_budget` fino a 25) e per q14 nessun match. Il richiamo basso con questi descrittori
  derivati meccanicamente dal frontmatter indica un problema di recupero o di budget delle card, da
  indagare prima di leggerlo come giudizio sul prodotto.
- I byte di B sono la dimensione del catalogo curato (solo id e descrizione) e quelli di C sono le
  risposte reali di ricerca, con riferimenti e hash; i due formati non sono identici, quindi il
  confronto è indicativo e sfavorisce C. Nessun costo di runtime è stato osservato.
