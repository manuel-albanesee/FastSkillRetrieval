# FastSkillRetrieval — Brainstorming architetturale
## Dalle skill al *Capability Retrieval*: ECC, Codex, Claude Code e SQLite

> **Stato:** knowledge base di brainstorming — non una specifica approvata e non un piano di implementazione definitivo.  
> **Data ricerca:** 17 ottobre 2026  
> **Ambito:** indicizzazione, recupero, composizione del contesto, attivazione, telemetria e manutenzione delle capacità degli agenti.  
> **Fonti:** documentazione ufficiale OpenAI/Codex, Anthropic/Claude Code, SQLite e repository ECC, elencate in [Fonti e riferimenti](#fonti-e-riferimenti).

---

## 0. Executive summary

L'idea iniziale di **FastSkillRetrieval** consiste nel recuperare selettivamente skill pertinenti alla query invece di esporre all'agente un catalogo sempre più esteso. La discussione porta a un'evoluzione: indicizzare non solo skill, ma **capacità eterogenee** (*capabilities*) con un registro comune, mantenendo distinti i modi in cui vengono rese effettivamente operative.

Una capability può essere: una skill, un subagent, un workflow, un comando, una regola, un contesto, un tool/MCP, un hook o una risorsa di memoria/istinto. **Essere presente nel database non significa essere eseguibile, autorizzata o necessariamente inserita nel prompt.** Il motore deve prima trovare la risorsa adatta, poi risolvere dipendenze, compatibilità e vincoli, infine scegliere il meccanismo corretto: *load, delegate, invoke, enforce, trigger* o *retrieve-as-data*.

**Decisioni già emerse nella conversazione**:

1. Non indicizzare deliberatamente una descrizione scadente. Un agente deve **correggere/normalizzare la descrizione prima del primo ingest**; la descrizione originale va comunque conservata per provenienza e audit.
2. Introdurre un **controllo schedulato o attivato da evento** che attraversi il catalogo, controlli le descrizioni, proponga correzioni, rigeneri solo gli indici interessati e verifichi la consistenza del risultato.
3. **Per ora non introdurre una misura LLM della qualità/pertinenza percepita.** Mantenere controlli **deterministici** su struttura, metadati, coerenza, duplicati e stato tecnico. Un punteggio di controllo non è una probabilità di corretta selezione da parte del modello.
4. Registrare almeno **quante volte ogni skill è stata recuperata** dalle query. Estendere lo stesso criterio agli altri tipi di capability. Le risorse poco recuperate diventeranno *candidate da esaminare*, non risorse da cancellare automaticamente.
5. Separare **selezione**, **inserimento nel contesto** ed **esecuzione effettiva**: specialmente per subagent, MCP e hook non sono operazioni equivalenti.

**Ipotesi architetturale proposta**: un *Capability Registry* SQLite con FTS5 per recupero lessicale, metadati tipizzati, collegamenti fra risorse, log degli eventi, adapter per Codex/Claude Code e una componente vettoriale opzionale. Un orchestratore separato decide cosa attivare. È il punto di partenza più piccolo che preserva la possibilità di evolvere verso retrieval ibrido, grafo delle dipendenze e politiche di ranking più sofisticate.

---

## 1. Problema da risolvere

### 1.1 Il problema non è «avere molte skill», ma «esporre quelle giuste»

Un numero elevato di definizioni comporta: rumore nelle descrizioni, possibili sovrapposizioni, consumo di contesto nella fase di discovery, descrizioni che competono sulla stessa intenzione, costi di manutenzione e difficoltà nel capire che cosa venga realmente usato.

La soluzione non può limitarsi a comprimere ogni `SKILL.md`: è più utile **separare il livello di discovery dal payload operativo** e caricare il secondo solo quando serve. Codex documenta esplicitamente il principio di *progressive disclosure*: in fase iniziale nome e descrizione della skill, contenuto completo dopo selezione [C1]. Claude Code usa anch'esso le descrizioni per decidere quando caricare una skill; il contenuto completo viene normalmente reso disponibile al momento dell'invocazione [L1].

### 1.2 Nuova definizione di «retrieval»

`query -> intent/task analysis -> candidate retrieval -> policy filters -> dependency resolution -> context plan -> runtime action -> telemetry`

L'output del motore non dovrebbe essere solo una lista di file, ma un **piano di capacità** in cui ogni elemento specifichi:

- *cosa* è stato selezionato;
- *perché* è ritenuto pertinente;
- *per quale runtime* è compatibile;
- *quali prerequisiti* richiede;
- *come* renderlo disponibile;
- *quanti token* presumibilmente richiederà;
- *quale livello di autorizzazione* è necessario;
- *quale versione* della definizione viene utilizzata.

### 1.3 Confini

**Il Registry decide cosa proporre; il runtime decide cosa può realmente caricare o eseguire.** Nessun risultato di retrieval concede nuovi privilegi. Il database non deve diventare una scorciatoia per bypassare file di policy, sandbox, approval, permission rules o approvazioni utente.

---

## 2. Cosa contiene ECC: inventario e significato

Il README del repository ufficiale **affaan-m/ECC** riportava, al momento della consultazione, **311 skill, 71 agent e 97 command shim legacy**; presenta anche rules, hook, memory e continuous learning [E1]. **Questi numeri sono una fotografia del README, non un inventario verificato file per file né un vincolo del progetto.** ECC è un repository di configurazioni/harness con adattatori per diversi ambienti, non una specifica universale che tutti i runtime interpretano allo stesso modo [E1].

| Tipo di risorsa | Ruolo | Indicizzabile? | Modalità di utilizzo prevista | Attenzione |
|---|---|---|---|---|
| **Skill** | Istruzioni e materiali riutilizzabili | Sì, priorità alta | `load_skill` | Descrizione e trigger distinguibili dal corpo completo |
| **Subagent** | Lavoratore specializzato con contesto proprio | Sì, priorità alta | `delegate` | Serve supporto di delega nel runtime |
| **Workflow** | Processo multi-step | Sì, priorità alta | `orchestrate` | Il grafo degli step non equivale a un comando eseguibile |
| **Command** | Entry point invocabile, spesso scorciatoia | Sì | `invoke_command` | Può essere alias di skill: evitare duplicati |
| **Rule** | Regola di progetto o di comportamento | Sì, con classi | `enforce` o `attach_context` | Le regole obbligatorie non dipendono dal ranking |
| **Context / Mode** | Profilo operativo (sviluppo, revisione, ricerca) | Sì | `select_mode` | Può sovrapporsi alle istruzioni persistenti |
| **MCP server/tool** | Accesso a dati e azioni esterne | Sì (manifest e metadati) | `invoke_tool`, previa disponibilità | Catalogare un tool non ne abilita l'uso |
| **Hook** | Intercettazione o reazione a eventi | Sì (come registro) | `trigger` lato runtime | Non è una skill attivabile via similarità |
| **Memory / instinct** | Pattern ed evidenze storiche | Sì, con ambito e scadenza | `retrieve_as_data` | Rischio istruzioni obsolete o non attendibili |
| **Adapter / plugin metadata** | Associazione fra definizione logica e piattaforma | Sì | `resolve_runtime_binding` | Percorsi, sintassi e semantica dipendono dalla piattaforma |

**Nota interpretativa:** `workflow`, `context`, `instinct` e `adapter` sono categorie del **nostro modello concettuale**; non bisogna assumere che ogni framework esponga primitive native con questi stessi nomi. Gli adapter devono dichiarare cosa è realmente supportato.

---

## 3. Ricerche puntuali: Codex

### 3.1 Skill: `SKILL.md`, descrizione e caricamento progressivo

La documentazione Codex descrive una skill come cartella con `SKILL.md` e file opzionali (`scripts/`, `references/`, `assets/`, eventualmente altre risorse). Nome e descrizione sono i metadati iniziali di discovery; il contenuto operativo viene letto quando la skill è selezionata [C1]. L'esempio ufficiale consiglia di chiarire **quando** la skill debba e **non debba** attivarsi [C1].

**Conseguenze per FastSkillRetrieval:**

- indicizzare la descrizione curata, trigger, controindicazioni, esempi e tag;
- preservare `SKILL.md` e asset come *payload referenziato*, non copiarli integralmente nell'indice testuale;
- mantenere riferimenti a percorso, origine, versione e hash;
- distinguere una skill disponibile nel catalogo da una skill attivata nella sessione.

### 3.2 Istruzioni persistenti: `AGENTS.md`

Codex utilizza `AGENTS.md` e file di override in una gerarchia che include istruzioni globali e di progetto; le istruzioni lungo il percorso del repository concorrono a definire il comportamento [C2]. Questi file non devono essere trattati come comuni suggerimenti facoltativi se rappresentano vincoli imposti dal progetto.

**Conseguenza:** il Registry può *catalogarli* e cercarne la provenienza, ma il compilatore del contesto deve rispettare la risoluzione gerarchica nativa. Una policy prescrittiva non viene omessa perché ha uno score semantico basso.

### 3.3 Subagent Codex

Codex documenta agenti built-in quali `default`, `worker` ed `explorer`, oltre a custom agents definiti con file TOML in `~/.codex/agents/` o `.codex/agents/` [C3]. Le definizioni possono comprendere descrizione, modello, effort e istruzioni specifiche [C3]. L'attivazione passa dall'orchestrazione del runtime.

**Conseguenza:** il nostro `subagent:code-reviewer` è un oggetto logico; un adapter Codex deve verificare che una definizione Codex valida sia installata/esposta prima di proporre `delegate` come azione effettiva. Se la definizione è solo un Markdown ECC per Claude, non si può presumere che Codex la esegua nativamente.

### 3.4 MCP, configurazione e permessi

Codex documenta connessioni MCP e configurazioni locali condivise fra i client appropriati [C4]. Le impostazioni di sandbox e approvazione rimangono indipendenti dalla scoperta delle capacità [C5]. Anche Codex documenta hook, ma **eventi, ambienti supportati e handler ammessi variano per superficie/runtime** [C6].

**Conseguenze:** introdurre nel binding attributi come `installed`, `connected`, `approved`, `supported_events`, `surface` e `effective_permissions`, risolti a runtime anziché affidarsi a un record statico del DB.

---

## 4. Ricerche puntuali: Claude Code

### 4.1 Skill: descrizione, invocabilità e budget

Claude Code usa il frontmatter `SKILL.md`; il campo `description` è fondamentale per la selezione e può essere integrato da `when_to_use` [L1]. Le skill possono essere invocabili dall'utente, dal modello o solo da uno dei due, secondo i relativi metadati. La documentazione chiarisce la differenza fra una descrizione visibile e il corpo caricato quando la skill viene invocata [L1].

**Conseguenza:** non è sufficiente `is_active = true`. Servono campi distinti, ad esempio `user_invocable`, `model_invocable`, `discovery_visible` e `load_strategy` (sempre derivati dalle regole effettive del runtime).

### 4.2 Subagent: contesto e strumenti indipendenti

Claude Code definisce i subagent come assistenti con **propria context window**, system prompt e accesso agli strumenti delimitato; la descrizione guida la delega [L2]. Il frontmatter supporta proprietà come `tools`, `disallowedTools`, `model`, `permissionMode`, `mcpServers`, `skills`, `hooks`, `memory`, `isolation` e altre, la cui compatibilità dipende dalla versione [L2]. È documentato anche il *preload* di skill nel contesto del subagent [L2].

**Conseguenze:**

- un subagent può **dipendere da** una o più skill;
- la scelta di un subagent può risparmiare contesto al main agent ma aumentare costo e utilizzo complessivo;
- i metadati devono indicare strumenti minimi, profilo di isolamento e capacità di spawn;
- per un subagent si conta separatamente *recuperato*, *proposto*, *lanciato* e *terminato*.

### 4.3 `CLAUDE.md`, rules e gerarchia

I file `CLAUDE.md` contengono istruzioni persistenti, con ambiti organizzativi, personali e di progetto. La documentazione distingue **istruzioni contestuali** da controlli realmente applicati tramite *managed settings*, permissions e sandbox [L3]. Claude Code documenta anche la lettura di `AGENTS.md` in condizioni e versioni specifiche; la risoluzione non è identica a quella di Codex [L3].

**Conseguenza:** se lo stesso repository contiene `AGENTS.md` e `CLAUDE.md`, la *politica di inclusione* non deve essere inventata dal retrieval. Il runtime adapter conosce la gerarchia; FastSkillRetrieval aggiunge solo contenuto opzionale che non confligga.

### 4.4 Hook e MCP

Claude Code documenta hook come `PreToolUse`, `PostToolUse` e altri eventi con payload e capacità differenti [L4]. In particolare un hook `PreToolUse` è collegato al lifecycle di una chiamata tool e non può essere sostituito da una semplice istruzione testuale. Per MCP documenta trasporti e configurazioni specifiche, fra cui server HTTP remoti e server locali stdio [L5].

**Conseguenza:** catalogare hook e MCP con `trigger`, `scope`, `runtime`, `permission_requirements` e `configuration_fingerprint`; **mai eseguire automaticamente codice trovato in una risorsa perché la ricerca l'ha reputata pertinente**.

---

## 5. Modello unificato: Capability Registry

### 5.1 Identità e versioni

Ogni capacità necessita di un identificatore logico stabile e di riferimenti alle sue rappresentazioni per piattaforma. Esempio:

```text
capability_id: ecc:agent:code-reviewer
kind: subagent
canonical_name: code-reviewer
source_provider: ECC
source_path: agents/code-reviewer.md
source_revision: <commit-o-versione>
content_sha256: <hash-contenuto>
status: active | quarantined | archived
```

**Identità ≠ versione ≠ deployment.** Il record rappresenta la funzione logica; una revisione ne conserva il contenuto; un *binding* attesta come quella revisione viene resa disponibile in un determinato ambiente. Serve inoltre una relazione `alias_of` per impedire che 97 command shim siano trattati come 97 competenze nuove, quando puntano a skill esistenti.

### 5.2 Metadati utili al retrieval

Per ogni risorsa, dove rilevanti:

- `name`, `kind`, `summary`, `indexed_description`, `original_description`, `description_revision`;
- `intent_tags`, `task_phrases`, `positive_triggers`, `negative_triggers`, `domains`, `languages`, `frameworks`;
- `expected_input`, `expected_output`, `side_effect_class` (`read`, `write`, `external_action`);
- `required_capabilities`, `required_tools`, `optional_tools`, `conflicts_with`;
- `origin`, `license`, `source_path`, `source_revision`, `content_hash`, `trust_level`;
- `runtime_compatibility`, `min_runtime_version`, `activation_mode`, `estimated_context_tokens`;
- `enabled`, `deprecated`, `requires_approval`, `tenant_scope`, `project_scope`;
- `indexed_at`, `last_retrieved_at`, `last_invoked_at`, `last_validated_at`.

Evitiamo di inserire segreti, token e credenziali nei metadati o negli embedding. I segreti devono rimanere nei sistemi di gestione credenziali del runtime.

### 5.3 Descriptor e payload

Tre strati distinti:

1. **Descriptor** — nome, descrizione, tag e trigger, economici da ricercare.
2. **Manifest** — prerequisiti, dipendenze, policy, compatibilità, istruzioni per binding.
3. **Payload** — file originali e riferimenti ai documenti/script; vengono caricati su domanda o eseguiti solo dal runtime autorizzato.

Un embedding può rappresentare il *descriptor* (ed eventualmente descrizioni alternative), senza rendere il payload operativamente attivo.

---

## 6. Ingestion: migliorare prima di indicizzare

La proposta iniziale di indicizzare descrizioni scadenti per poi assegnare loro un voto **è stata scartata**. Il ciclo corretto è:

```text
Discover source
  -> Parse metadata + source body
  -> Normalize / enrich description (agent)
  -> Deterministic validation gates
  -> Check source provenance & compatibility
  -> Human review if high-risk/ambiguous
  -> Persist descriptor revision + original payload link
  -> FTS index (+ optional embeddings)
  -> Integrity check -> Publish
```

### 6.1 Cosa deve migliorare l'agente

- spiegare l'azione e il risultato atteso, non solo il dominio;
- esplicitare quando è utile, e quando **non** lo è;
- citare ambienti/linguaggi/tool realmente supportati;
- distinguere capacità simili, identificando il confine operativo;
- non inventare prerequisiti, autorizzazioni o funzionalità;
- non modificare il corpo sorgente senza un'operazione separata e autorizzata;
- conservare il testo originale e motivare eventuali aggiunte.

**Distinzione importante:** una descrizione ottimizzata per il retrieval non deve necessariamente sostituire il campo `description` del file originale. Può rimanere una **proiezione indicizzata** fino a revisione/approvazione. Questo evita modifiche silenziose a repository esterni.

### 6.2 Controlli deterministici (attuali)

Controllare: parsing frontmatter, schema, campi obbligatori, caratteri non validi, dimensione, termini vuoti, duplicati esatti, collisioni di identificatori, coerenza `kind`/`activation_mode`, file mancanti, hash, riferimenti non risolti, dipendenze circolari, compatibilità non dichiarata e payload considerato ad alto rischio.

**NON** chiamare il risultato «probabilità che Claude/Codex comprenda la skill». I controlli deterministici certificano proprietà verificabili, non la bontà della selezione semantica del modello. Le prove con richieste positive/negative restano un possibile esperimento successivo, fuori dal perimetro iniziale.

---

## 7. Retrieval: dal match all'attivazione

### 7.1 Pipeline proposta

```text
Utente / richiesta
  |
  v
[Task parser leggero: intent, linguaggio, dominio, progetto]
  |
  v
[Candidate generation: FTS5 + eventuale vettoriale]
  |
  v
[Deterministic filtering: runtime, versioni, scope, permessi, status]
  |
  v
[Hybrid rank / dedup / alias collapse]
  |
  v
[Dependency closure: skill, tool, rule, subagent]
  |
  v
[Context budget & action planning]
  |
  +--> load_skill      -> istruzioni on demand
  +--> delegate        -> subagent del runtime
  +--> invoke_tool     -> tool/MCP configurato e autorizzato
  +--> orchestrate     -> workflow / step plan
  +--> attach_context  -> note opzionali non obbligatorie
  +--> enforce/trigger -> soltanto per meccanismi nativi di policy/evento
  |
  v
[Events + telemetry + feedback]
```

### 7.2 Candidate generation

- **Baseline:** ricerca FTS5 su nome, descrizione, trigger e tag. Garantisce ricerca lessicale spiegabile e non richiede un modello di embedding.
- **Estensione:** ricerca vettoriale sulle descrizioni approvate. È utile per parafrasi e sinonimi ma non garantisce correttezza operativa.
- **Ibrido:** combinare liste lessicali e vettoriali con **Reciprocal Rank Fusion (RRF)** o strategia simile; non sommare direttamente punteggi BM25 e cosine senza calibrarli.
- **Alias collapse:** deduplicare la stessa competenza rappresentata da skill, command alias e diverse distribuzioni.
- **Filtri rigidi prima dell'attivazione:** non proporre come eseguibile un tool assente o un agente privo di supporto nel runtime.

### 7.3 Budget contestuale

Il planner deve stimare due budget distinti: **token del main agent** e **costo/contesto degli eventuali subagent**. Il retrieval superficiale può restituire fino a `K` descrittori, ma la fase finale carica solo i corpi indispensabili. Si deve preferire il set minimo di capacità che copre il task e i suoi prerequisiti; non semplicemente le prime `K` risorse più simili.

### 7.4 Risoluzione dei conflitti

Ordine logico suggerito (da verificare per ogni harness): **policy e permission del runtime > istruzioni obbligatorie di progetto > capacità opzionali selezionate > preferenze di ranking**. Se due skill danno istruzioni incompatibili, l'assembler segnala il conflitto o sceglie in base ad ambito/versione, non le incolla entrambe alla cieca.

---

## 8. SQLite: scelta del database e motivazioni

SQLite è adatto a un **registry locale/embedded**, con query, indici, metadati, vincoli e log semplici. Il modulo **FTS5** fornisce full-text search e ranking `bm25()` [S1]. Le funzioni JSON consentono metadati flessibili [S3]. Tabelle `STRICT`, chiavi esterne, UPSERT e transazioni sono utili per un catalogo tipizzato e aggiornato incrementalmente [S4][S5][S6].

SQLite **non dispone nativamente di ricerca vettoriale equivalente a un vector DB specializzato**. Una possibilità è l'estensione di terze parti `sqlite-vec`, dichiarata dal progetto stesso ancora *pre-v1*, con possibile evoluzione incompatibile dell'API [V1]. L'adapter vettoriale deve quindi essere opzionale e sostituibile; per un MVP si può partire da FTS5.

### 8.1 Cautele tecniche

- Abilitare `PRAGMA foreign_keys=ON` **per ogni connessione** [S5].
- WAL facilita letture contemporanee a una scrittura, ma **rimane un singolo writer** [S2].
- Gestire `busy_timeout`, transazioni corte, indici sulle foreign key e checkpoint, secondo carico reale.
- Per backup consistente usare la **SQLite Backup API** o un metodo documentato che tenga conto del WAL [S7]; evitare una copia grezza e non coordinata di un DB aperto.
- Versionare schema tramite migrazioni e `PRAGMA user_version`; non fare evoluzioni nascoste al primo avvio.
- Non abilitare estensioni native non attendibili o condividere un file DB in scrittura fra host remoti senza un disegno adatto.
- Pianificare una migrazione di backend se diventano necessari molti writer distribuiti, isolamento multi-tenant complesso, query vettoriali enormi o replicazione in tempo reale.

### 8.2 Schema concettuale suggerito

| Entità | Perché serve | Chiave / legami principali |
|---|---|---|
| `capabilities` | Identità stabile, categoria, stato, nome | `id`, `kind`, `status` |
| `capability_revisions` | Originale e revisione indicizzata con hash | `capability_id`, `revision`, `sha256` |
| `runtime_bindings` | Mappatura Codex/Claude/ECC e disponibilità | `capability_id`, `runtime`, `binding_path` |
| `capability_relations` | `requires`, `alias_of`, `conflicts_with`, `invokes` | `source_id`, `target_id`, `relation_kind` |
| `capability_descriptors_fts` | FTS5 per nome/descrizione/trigger/tag | `rowid` associato a descriptor |
| `embedding_records` | Identità di vettore, modello, dimensione/versione | `revision_id`, `embedding_model`, `vector_ref` |
| `retrieval_queries` | Traccia anonima/minimizzata della ricerca | `query_id`, `runtime`, `timestamp` |
| `retrieval_events` | Impression, selezione, invocazione, outcome | `query_id`, `capability_id`, `event_type` |
| `maintenance_runs` | Scheduler, motivi, esito e cambiamenti | `run_id`, `trigger`, `started_at` |
| `maintenance_findings` | Drift, duplicati, anomalie, suggerimenti | `run_id`, `capability_id`, `finding_type` |

**Nota:** il modello è concettuale, non uno schema SQL finale. Il catalogo deve poter funzionare anche senza `embedding_records` e senza estensioni di terze parti.

### 8.3 Piccoli esempi SQL (illustrativi, non migration production-ready)

```sql
-- Esatta semantica da adattare allo schema reale.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE capabilities (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN
    ('skill','subagent','workflow','command','rule','context','tool','hook','memory','adapter')),
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','quarantined','archived')),
  retrieval_count INTEGER NOT NULL DEFAULT 0 CHECK (retrieval_count >= 0),
  invocation_count INTEGER NOT NULL DEFAULT 0 CHECK (invocation_count >= 0)
) STRICT;

CREATE TABLE capability_relations (
  source_id TEXT NOT NULL REFERENCES capabilities(id),
  target_id TEXT NOT NULL REFERENCES capabilities(id),
  relation_kind TEXT NOT NULL,
  PRIMARY KEY (source_id, target_id, relation_kind)
) STRICT;

-- Indice FTS5 separato, con descriptor testuali;
-- sincronizzazione applicativa o tramite trigger da progettare.
CREATE VIRTUAL TABLE capability_descriptors_fts USING fts5(
  capability_id UNINDEXED,
  name,
  description,
  trigger_phrases,
  tags,
  tokenize = 'unicode61'
);

-- FTS5 ha una convenzione di ranking da leggere correttamente:
SELECT capability_id, bm25(capability_descriptors_fts) AS bm25_score
FROM capability_descriptors_fts
WHERE capability_descriptors_fts MATCH 'review AND security'
ORDER BY bm25_score ASC
LIMIT 10;
```

FTS5 usa valori `bm25` con ordinamento **ascendente per i match migliori** nella sua implementazione, dettaglio che evita un ranking invertito [S1]. La query FTS richiede sanitizzazione/normalizzazione corretta dell'input: non inserire ingenuamente la stringa grezza dell'utente come espressione `MATCH`.

### 8.4 Perché eventi + contatori, non solo contatori

I contatori sono ottimi per la dashboard ma non permettono di ricostruire *quando, per quale query e a quale posizione* una risorsa è stata considerata. Un log compatto consente statistiche retrospettive, confronti pre/post revisione e verifica di possibili conteggi duplicati. I contatori aggregati possono essere aggiornati transazionalmente come proiezioni degli eventi o ricostruiti periodicamente.

---

## 9. Telemetria: misurare il percorso della capability

Un solo `retrieval_count` è un buon primo requisito, ma è importante definirne il significato:

| Evento | Definizione suggerita | Perché conta |
|---|---|---|
| `candidate_generated` | Entrata nel set grezzo FTS/vettoriale | Diagnosi del motore, alto volume |
| **`retrieved`** | Entrata nel set finale restituito al planner | **Contatore concordato come base** |
| `selected` | Scelta nel piano delle capacità | Elimina falsi positivi del top-K |
| `context_loaded` | Corpo effettivamente caricato | Misura token e contesto reale |
| `invocation_started` | Delega/tool/command effettivamente avviato | Misura utilizzo operativo |
| `invocation_completed` | Terminazione, successo tecnico o errore | Affidabilità runtime |
| `task_outcome_verified` | Risultato validato con segnale definito | Misura qualità solo se verificabile |

**Invariante suggerita:** contare `retrieved` **una sola volta per `query_id` + `capability_id` + fase**, anche se la stessa capacità compare nella lista FTS, nella vettoriale e nella chiusura delle dipendenze. Per i subagent, il conteggio di retrieval non equivale ad aver avviato un agent thread.

### 9.1 Metriche derivate

- `retrievals_7d / 30d / 90d`, `selected / retrieved`, `invocations / selected`;
- `time_since_last_retrieval`, `error_rate` solo sugli eventi invocati;
- `cost_tokens_when_loaded` e `mean_context_payload_tokens`;
- `source_revision -> description_revision -> retrieval_trend`;
- `exposure`: quante query pertinenti sono state processate mentre la risorsa era eleggibile e disponibile.

**Precauzione epistemica:** `retrieval_count = 0` **non dimostra** che una skill sia inutile o abbia una descrizione errata. Può essere nuova, rara, non applicabile ai task osservati, filtrata da policy, disabilitata, o surclassata da alias/ranking. Una skill di emergenza quasi mai utilizzata può essere preziosa.

### 9.2 Come usare i contatori per la manutenzione

1. Identificare *candidate* con pochi recuperi, non cancellarle.
2. Separare risorse vecchie/nuove e frequenti/di nicchia.
3. Verificare competenze sovrapposte e alias che assorbono il traffico.
4. Confrontare descrizione, trigger e classi di richieste realmente osservate, senza sovra-interpretare i numeri.
5. Proporre una revisione, conservarne la versione precedente, reindicizzare e confrontare intervalli comparabili.
6. Archiviare solo con criterio esplicito, valutazione dell'impatto e possibilità di rollback.

Per ora questa analisi può essere **deterministica e descrittiva**. Eventuali test `should-trigger`/`should-not-trigger`, menzionati anche dalla documentazione Claude Code [L1], sono un passo futuro e richiedono un dataset controllato e metriche separate.

---

## 10. Manutenzione schedulata o triggerata

Il «timer» discusso è un **evento schedulato oppure un trigger**, non un countdown nell'interfaccia. Quando scatta, avvia un processo di manutenzione del catalogo.

### 10.1 Trigger possibili

- Scheduler, ad esempio controllo notturno o settimanale.
- File watcher: aggiunta/modifica/eliminazione di skill o agent definition.
- Nuova release/commit upstream ECC.
- Installazione/disinstallazione plugin o modifica della configurazione di runtime.
- Drifting di disponibilità di un MCP o di un tool.
- Soglia di inattività, incremento errori o anomalia nei conteggi.
- Esecuzione manuale `reconcile` dopo una modifica importante.

### 10.2 Ciclo di manutenzione suggerito

```text
Event / Scheduler
  -> Acquire exclusive maintenance lease
  -> Read catalog and upstream file hashes
  -> Inspect changes / stale descriptors / invalid links
  -> Deterministic checks
  -> If needed: agent proposes description revision
  -> Policy/provenance checks + optional review
  -> Compute diffs and embedding invalidations
  -> Atomic publish of new descriptor/index revisions
  -> Read-back consistency validation
  -> Write maintenance findings and counters
  -> Release lease
```

**Non reindicizzare tutto per ogni tick.** Confrontare hash e versioni; ricalcolare embedding soltanto se cambia il testo indicizzato o il modello di embedding. Un job deve essere *idempotente* e poter ripartire dopo un errore senza duplicare revisioni o contatori. La pubblicazione dovrebbe risultare atomica dal punto di vista delle query: mai lasciare una capability attiva senza il descriptor coerente.

### 10.3 Frequenza (proposta, non decisione)

- Trigger su cambiamento sorgente: prioritario.
- Riconciliazione giornaliera leggera: integrità, stato e hash.
- Revisione più lenta, per esempio settimanale: poco utilizzo e descrizioni migliorabili.
- Full rebuild: manuale o quando cambia il modello di embedding/lo schema di indicizzazione.

---

## 11. Relazioni tra capacità: un «capability graph» leggero

Il Registry dovrebbe conoscere almeno queste relazioni:

- `requires` — dipendenza operativa obbligatoria;
- `uses` — tool o skill usata comunemente;
- `preloads` — subagent inizializza il contesto con una skill;
- `invokes` — comando/workflow richiama una capability;
- `alias_of` — rappresentazione alternativa, senza contare una competenza duplicata;
- `conflicts_with` — istruzioni incompatibili nello stesso contesto;
- `supersedes` — revisione o alternativa preferita;
- `governed_by` — regola/policy applicabile.

Esempio per una richiesta di *security review* in .NET:

```text
Query «Analizza questo controller .NET e individua vulnerabilità»
   -> subagent:code-reviewer  [delegate]
      -> requires tool:read-file [runtime availability]
      -> uses skill:secure-code-review [load in subagent if supported]
      -> governed_by rule:project-security [mandatory]
   -> tool:grep               [invoke only if authorized]
   -> summary -> main agent    [bounded result]
```

L'esempio rappresenta una **composizione ipotetica**, non dichiara che ECC includa esattamente ogni nodo con questi identificatori né che ogni platform adapter supporti il preload nello stesso modo. Una traversata di dipendenze è una piccola forma di graph traversal su tabelle relazionali; **non richiede Neo4j** finché volume e query sono contenuti.

---

## 12. Adapter Codex ↔ Claude Code: contratti differenti

| Aspetto | Codex | Claude Code | Regola di progetto |
|---|---|---|---|
| Skill | `SKILL.md`, disclosure progressivo [C1] | `SKILL.md`, discovery/invocabilità [L1] | Descriptor comune + flag per runtime |
| Istruzioni persistenti | `AGENTS.md` e override [C2] | `CLAUDE.md` e lettura condizionata di `AGENTS.md` [L3] | Non sovrascrivere la gerarchia nativa |
| Custom subagent | File TOML, ad esempio `.codex/agents` [C3] | Markdown con frontmatter, `.claude/agents` [L2] | Definizione logica + binding specifico |
| Subagent skill preload | Dipende dalla configurazione/percorso del runtime | Campo `skills` documentato [L2] | Feature flag, mai presunta |
| MCP | Configurazione MCP del client [C4] | Server HTTP/stdin e scopes [L5] | Verifica connessione/permessi live |
| Hooks | Documentati con limiti per superficie [C6] | Lifecycle e decision control propri [L4] | Mappare eventi uno per uno |
| Permissions | Sandbox/approval profiles [C5] | Permission mode/managed settings [L3] | **Mai** sostituire enforcement con prompt |

Una capability può trovarsi in uno dei seguenti stati per singolo runtime:

```text
not_supported | supported_not_installed | installed_unavailable |
available_unapproved | available_approved | disabled_by_policy
```

Gli stati runtime devono essere rilevati quando serve; non sono verità permanenti memorizzabili una volta per tutte.

---

## 13. Sicurezza, fiducia e supply chain

Questa architettura indicizza istruzioni provenienti anche da repository terzi. La domanda non è solo «quanto è rilevante?» ma **«posso fidarmi e che cosa le permetto di fare?»**.

Vincoli proposti:

1. Provenienza e hash verificabili, con versione sorgente bloccabile (*pin*).
2. Distinzione `untrusted metadata` / `trusted local policy` / `runtime enforcement`.
3. Nessuna esecuzione implicita di script, hook o comando durante l'ingestion.
4. `requires_approval` per azioni esterne, scrittura e cambi configurazione.
5. Nessuna descrizione o file remoto può chiedere al retrieval di scavalcare system instruction, policy aziendali, sandbox e approval.
6. Sanitizzazione degli input nelle query SQL/FTS e dei metadati riusati nei prompt.
7. Revisione esplicita per cambiamenti di `side_effect_class`, nuovi permessi, nuove dipendenze, nuovi comandi.
8. Isolamento di progetti/tenant per impedire il leakage di istruzioni, file e log.
9. Log minimizzati: evitare di salvare query contenenti segreti o codice riservato in chiaro senza consenso e retention definiti.
10. Possibilità di quarantena e rollback immediati per capability alterate o compromesse.

L'agente che migliora le descrizioni opera su **materiale non attendibile** e produce una proposta validata: non acquisisce il diritto di modificare policy o installare tool.

---

## 14. Scenari di errore da progettare

| Scenario | Comportamento desiderato |
|---|---|
| Skill rilevante ma non disponibile nel runtime | Proporre fallback o spiegare indisponibilità, senza simulare un load |
| Subagent trovato ma definizione non installata | Non contare un'invocazione; suggerire binding esplicito |
| MCP elencato ma disconnesso | Non chiamare il tool; conservare causa nel log tecnico |
| Due descrizioni indicizzate rappresentano lo stesso comando | Collassare alias e rendere visibile la fonte scelta |
| Una regola obbligatoria non compare tra i top match | Includerla comunque tramite risoluzione di policy nativa |
| Il job di manutenzione fallisce dopo il nuovo embedding | Non pubblicare revisioni incoerenti; mantenere ultima versione stabile |
| Cambio modello embedding | Costruire nuovo indice versionato e passare a esso solo dopo validazione |
| Skill poco recuperata | Aprire finding diagnostico, non cancellare |
| Retrieval restituisce 0 match | Risposta trasparente e percorso fallback; non inventare una capability |
| Una revisione promette strumenti non presenti nel sorgente | Quarantena/invalidazione del descriptor arricchito |
| Context budget non sufficiente | Ridurre optional payload, non rimuovere policy obbligatorie |

---

## 15. Roadmap concettuale (ordine di rischio, non backlog approvato)

### Fase A — Registry minimo

**Obiettivo:** catalogare skill ECC e recuperarle in modo prevedibile.

- Import con hash, originale, descrizione curata, schema e status.
- SQLite con FTS5 e ricerca lessicale.
- Contatore `retrieved` ben definito, query ID e deduplicazione degli eventi.
- Aggiornamento incrementale e job di manutenzione.

**Da considerare conclusa quando:** l'import ripetuto non duplica record, le descrizioni non valide non vengono pubblicate, una query ottiene risultati spiegabili e i contatori sono riproducibili.

### Fase B — Estendere alle capability

**Obiettivo:** aggiungere subagent e workflow senza confondere retrieval ed esecuzione.

- Tipo risorsa e adapter Codex/Claude.
- `runtime_bindings` e dipendenze.
- Piano d'azione `load / delegate / orchestrate`, inizialmente anche in modalità *dry-run*.
- Telemetria delle selezioni e avvii.

**Da considerare conclusa quando:** un agente non supportato non viene presentato come invocabile, e le dipendenze non vengono caricate se assenti/negate.

### Fase C — Ibrido e contesto intelligente

**Obiettivo:** aumentare recall senza sacrificare precisione e permessi.

- Adapter vettoriale opzionale con embedding versionati.
- FTS5 + vettoriale con fusione delle classifiche.
- Collasso alias/duplicati, ottimizzazione del budget, detector di conflitti.
- Monitoraggio qualità su eventi di selezione e utilizzo.

**Da considerare conclusa quando:** un benchmark controllato mostra un beneficio misurabile contro FTS5-only, senza regressioni in compatibilità, costo o sicurezza.

### Fase D — Catalogo esteso e manutenzione adattiva

**Obiettivo:** aggiungere rules, contexts, MCP, hooks, memory con semantiche corrette.

- Ingestion mirata per categoria.
- Distinzione fra policy fisse e note contestuali.
- Health check runtime e manutenzione guidata da telemetria.
- Eventuali valutazioni di attivazione positiva/negativa introdotte **solo quando desiderate**.

**Da considerare conclusa quando:** il Registry rimane consultabile anche se MCP/hook non sono connessi, senza mai fingere l'applicazione di un controllo che il runtime non supporta.

---

## 16. Questioni aperte del brainstorming

1. **Dove vive il motore?** Plugin locale, servizio MCP, wrapper attorno ai coding agent, libreria embedded o servizio ibrido? Quali punti di integrazione consentono davvero di intervenire nella discovery nativa?
2. **Quale è la fonte di verità?** Repository ECC, directory locali, configurazioni dell'utente o un registry che li osserva tutti?
3. **Chi approva le descrizioni curate?** Automazione completa per descrizioni low-risk e review obbligatoria per privilegi/side effect?
4. **Che cosa significa «recuperato»?** Top-K consegnato al planner o qualunque candidato apparso in FTS? Decisione proposta: solo il set finale restituito al planner.
5. **Come evitare exposure bias?** La popolarità nel ranking non deve rendere invisibili capacità rare o nuove. Servirà almeno distinguere esposizione, eleggibilità e invocazione.
6. **Quale policy di aggiornamento upstream?** Seguire commit/release, pin della versione, diff e rollback.
7. **Quale criterio di successo?** Per ora esclusivamente consistenza deterministica e contatori. Misure end-to-end potranno seguire con casi di prova controllati.
8. **Come gestire la portabilità?** Una skill è spesso trasferibile; hook, agent TOML, tool grants e settings non lo sono automaticamente.
9. **Quando SQLite non basta?** Stabilire soglie su writer concorrenti, volume, latenza di ricerca, dimensione vettori e multiutenza prima di decidere per un DB server.
10. **Come limitare l'iniezione di istruzioni?** Il catalogo deve etichettare provenienza, trust e confini fra *data* e *instructions*.
11. **Quando fare auto-rewrite?** Anche se la normalizzazione prima dell'ingestion è parte della proposta, decidere se aggiornare solo l'indice o il file sorgente tramite PR/review.
12. **Che cosa si può effettivamente controllare nel runtime?** Un adapter può limitarsi a offrire metadati se non esiste una API per il routing o la delega; questa limitazione deve essere esplicita.

---

## 17. Decision log sintetico

| Tema | Stato | Decisione / ipotesi |
|---|---|---|
| Descrizioni scadenti | **Deciso nel brainstorming** | Correzione/normalizzazione prima dell'ingestion; conservare originale |
| Rating semantico LLM | **Rimandato** | Solo controlli deterministici per ora |
| Telemetria | **Deciso nel brainstorming** | Contatore per recupero, per ogni capability; utile integrare eventi distinti |
| Controlli ricorrenti | **Deciso nel brainstorming** | Job schedulato o triggerato che verifica e reindicizza incrementi |
| Database | **Ipotesi da verificare** | SQLite + FTS5 per MVP |
| Ricerca vettoriale | **Opzionale** | Adapter separato, non prerequisito |
| Tipi da integrare per primi | **Proposto** | Skill -> subagent -> workflow |
| Sicurezza | **Vincolo architetturale** | Retrieval non aggira runtime policies/permissions |
| Context assembly | **Proposto** | Piano minimo di capacità, non concatenazione indiscriminata |
| Decommission capability inutilizzate | **Non deciso** | Mai rimuovere automaticamente per basso conteggio |

---

## 18. Sintesi finale

L'ipotesi più promettente per FastSkillRetrieval non è costruire un «database pieno di prompt». È costruire un **indice operativo delle capacità** che conosca *quando* una risorsa è utile, *a che cosa* serve, *con che cosa* è compatibile e *come* viene effettivamente attivata.

Il vantaggio competitivo potenziale nasce da quattro elementi combinati:

1. **Discovery leggera** per ridurre rumore e consumo di contesto.
2. **Capability graph** per comporre skill, subagent e tool senza duplicazioni.
3. **Portabilità controllata** mediante adapter Codex/Claude, senza fingere che i due runtime siano identici.
4. **Ciclo di miglioramento osservabile** con descrizioni curate prima dell'ingest, manutenzione incrementale e metriche di utilizzo, senza introdurre valutazioni qualitative premature.

Il principio guida resta: **selezionare la capacità giusta, con il minimo contesto necessario, senza confondere conoscenza, autorizzazione ed esecuzione**.

---

## Fonti e riferimenti

> Le fonti seguenti supportano i *fatti di prodotto e database*. Le strutture, gli stati e i processi etichettati come proposte nel documento sono **ragionamenti progettuali originali**, non funzionalità già offerte congiuntamente da ECC/Codex/Claude/SQLite. Documentazione consultata il **17/10/2026**; alcuni dettagli possono dipendere dalle versioni installate.

### ECC

- **[E1]** Repository ufficiale Everything Claude Code / ECC — catalogo, tipologie di componenti, numeri dichiarati e struttura: https://github.com/affaan-m/ECC

### Codex / OpenAI

- **[C1]** Build skills — `SKILL.md`, progressive disclosure e scoperta: https://learn.chatgpt.com/docs/build-skills
- **[C2]** Custom instructions with `AGENTS.md` — gerarchia e override: https://learn.chatgpt.com/docs/agent-configuration/agents-md
- **[C3]** Subagents — agenti integrati e personalizzati TOML: https://learn.chatgpt.com/docs/agent-configuration/subagents
- **[C4]** Model Context Protocol — configurazione e collegamenti MCP: https://learn.chatgpt.com/docs/extend/mcp
- **[C5]** Config basics — sandbox e approvazioni: https://learn.chatgpt.com/docs/config-file/config-basic
- **[C6]** Hooks — eventi, possibilità e limitazioni di ambiente: https://learn.chatgpt.com/docs/hooks
- **[C7]** OpenAI Skills / API — formati e versionamento dei bundle: https://developers.openai.com/api/docs/guides/tools-skills

### Claude Code / Anthropic

- **[L1]** Extend Claude with skills — discovery, frontmatter, invocabilità: https://code.claude.com/docs/en/skills
- **[L2]** Create custom subagents — contesto, descrizioni, tool, skills preloaded: https://code.claude.com/docs/en/sub-agents
- **[L3]** How Claude remembers your project — `CLAUDE.md`, rules, `AGENTS.md`, managed settings: https://code.claude.com/docs/en/memory
- **[L4]** Hooks reference — lifecycle e tool interceptors: https://code.claude.com/docs/en/hooks
- **[L5]** Connect Claude Code to tools via MCP: https://code.claude.com/docs/en/mcp

### SQLite e vettori

- **[S1]** SQLite FTS5 — full-text search, `MATCH`, BM25: https://www.sqlite.org/fts5.html
- **[S2]** SQLite WAL — letture/scritture concorrenti e singolo writer: https://www.sqlite.org/wal.html
- **[S3]** SQLite JSON functions — JSON nei metadati: https://www.sqlite.org/json1.html
- **[S4]** SQLite STRICT tables — tipizzazione rafforzata: https://www.sqlite.org/stricttables.html
- **[S5]** SQLite Foreign Keys — attivazione e vincoli: https://www.sqlite.org/foreignkeys.html
- **[S6]** SQLite UPSERT — aggiornamenti su conflitto: https://www.sqlite.org/lang_upsert.html
- **[S7]** SQLite Backup API — copie coerenti: https://www.sqlite.org/backup.html
- **[S8]** SQLite Generated Columns — alternative per indicizzazione di proprietà derivate: https://www.sqlite.org/gencol.html
- **[V1]** `sqlite-vec` (progetto terzo, pre-v1) — ricerca vettoriale: https://github.com/asg017/sqlite-vec

