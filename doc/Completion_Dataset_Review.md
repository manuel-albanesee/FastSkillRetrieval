# FastSkillRetrieval — dataset ECC di completamento da revisionare

Questa proposta contiene 24 gruppi semantici nuovi, ciascuno con una richiesta italiana e la sua traduzione inglese (48 casi), e 3 gruppi pilot separati (6 casi). Le etichette sono proposte dell'agente, **non approvate da una persona**: `human_review`, `approval` e `budget` rimangono `null`. La precedente revisione umana delle 20 richieste storiche non viene trasferita a questi casi.

I file autorevoli sono `examples/experiments/completion-v1.json` e `pilot-v1.json`. Il protocollo usa esattamente i 100 riferimenti e gli SHA-256 di `examples/capability-eval-proposal.json`. Non è una copia delle sue 20 richieste: introduce gruppi e formulazioni nuove. Il corpus resta limitato ai 100 manuali pinned; non rappresenta tutte le skill ECC.

## Separazione e limiti della valutazione

La divisione per gruppo è stabilita nell'artefatto prima di qualsiasi tuning: i primi 16 gruppi sono development (32 casi), gli ultimi 8 holdout (16 casi). I 3 gruppi pilot sono disgiunti e hanno split `pilot`. Le due lingue di un gruppo condividono sempre lo stesso split, essential e acceptable. L'ordine è una scelta esplicita di authoring, non un campionamento casuale o una stima rappresentativa dell'universo dei task.

**Il dataset, compreso il cosiddetto holdout, è pubblico e revisionabile. Non è un holdout protetto, segreto o indipendente dall'autore.** Non sono stati eseguiti tuning né misurazioni di qualità sui casi holdout. I test automatici verificano soltanto contratto, consistenza e struttura del dataset; non recuperano o valutano skill sulle query. Un'eventuale valutazione futura può descrivere solo questi casi osservati. Per affermare generalizzazione serve un nuovo set indipendente conservato fuori dal materiale accessibile durante lo sviluppo.

## Criterio delle etichette

`essential` indica il manuale direttamente richiesto dalla capacità descritta; `acceptable` identifica materiale complementare e non sostituisce automaticamente l'essenziale. Un'etichetta non significa che il manuale sia stato caricato, applicato o seguito. Ogni caso include una rationale fondata su argomenti effettivamente presenti nel manuale pinned; ad esempio `mcp-dependency-review` riguarda specificamente selector di pacchetto mutabili nella configurazione MCP, mentre `skill-comply` contempla scenari a tre livelli di pressione del prompt e timeline dei tool.

Tutti i task sono `declarative`: chiedono specifiche, checklist, strategie o piani testuali. `resources: []` significa che non richiedono fixture esterne per il task descritto, non che i manuali siano autosufficienti per eseguire tutti i workflow che documentano. Non è stato introdotto un confine di esecuzione filesystem o di rete verificato.

| Gruppo | Split | Essential proposta |
|---|---|---|
| api-pagination | development | ecc/api-design |
| migration-rename | development | ecc/database-migrations |
| query-index | development | ecc/postgres-patterns |
| python-fixtures | development | ecc/python-testing |
| rust-errors | development | ecc/rust-patterns |
| react-component | development | ecc/react-testing |
| react-waterfall | development | ecc/react-performance |
| accessible-dialog | development | ecc/accessibility |
| adr-storage | development | ecc/architecture-decision-records |
| onboarding-map | development | ecc/codebase-onboarding |
| connector-pattern | development | ecc/api-connector-builder |
| schema-drift | development | ecc/contract-first |
| agent-untrusted | development | ecc/agent-security-hardening |
| agent-failure-layer | development | ecc/agent-architecture-audit |
| tool-action-space | development | ecc/agent-harness-construction |
| cost-routing | development | ecc/cost-aware-llm-pipeline |
| agent-comparison | holdout pubblico | ecc/agent-eval |
| skill-portability | holdout pubblico | ecc/skill-host-compat |
| skill-compliance | holdout pubblico | ecc/skill-comply |
| mcp-static-review | holdout pubblico | ecc/mcp-dependency-review |
| incident-buttons | holdout pubblico | ecc/click-path-audit |
| production-readiness | holdout pubblico | ecc/production-audit |
| quality-capa | holdout pubblico | ecc/quality-nonconformance |
| prompt-advisory | holdout pubblico | ecc/prompt-optimizer |
| pilot-go-tests | pilot | ecc/golang-testing |
| pilot-fastapi | pilot | ecc/fastapi-patterns |
| pilot-skill-discovery | pilot | ecc/skill-scout |

## Provenienza e risorse

Il controllo offline ha ricalcolato SHA-256 sui byte originali di ciascuno dei 100 `SKILL.md`, letti dal fixture locale `C:/git/FastSkillRetrieval/.work/real-eval-fixture-4/sources`: **100/100 corrispondono al corpus pinned**. Non è stato modificato alcun manuale. Sono state lette le descrizioni dei 100 manuali e controllati i contenuti pertinenti dei manuali usati dalle nuove essential.

`examples/experiments/corpus-audit-v1.json` conserva gli hash verificati e i candidati di collegamenti relativi non risolti. Il controllo lessicale ha individuato 16 target assenti: include percorsi reali come `references/approval-ledger.sql`, `references/approval_claims.py`, `../../rules/react/testing.md`, oltre a link dimostrativi e falsi positivi di snippet (`url`, `t`, ADR di esempio). Il conteggio **non equivale a 16 dipendenze di runtime mancanti**. I riferimenti esterni non sono stati scaricati; il controllo non dimostra la presenza di risorse citate solo nel testo o in comandi shell.

I percorsi `skills/.../SKILL.md` nel protocollo sono relativi alla radice di corpus che l'operatore deve predisporre: il fixture usato per la verifica ha invece direttamente le directory dei manuali sotto `sources/`. Il corpus non è incluso nei template e non si presuppone che quei percorsi siano risolvibili dal checkout corrente.

## Configurazione ed esecuzione futura

`config-v1.json` rende esplicite le decisioni di authoring, limite dei risultati, feedback disabilitato e policy di split. Il `config_sha256` dei due protocolli è lo SHA-256 **dei byte esatti di quel file**. È una configurazione di proposta, dichiarata come tale, e non prova la configurazione effettiva di un runtime. Se si passa a una configurazione effettiva, occorre aggiornarne il pin prima della revisione e dell'autorizzazione.

`client.version` e `client.model` sono rispettivamente `REPLACE_WITH_OBSERVED_VERSION` e `REPLACE_WITH_OBSERVED_MODEL`: sono placeholder espliciti e non versioni o modelli osservati. Prima di un'esecuzione l'operatore dovrà compilare i pin effettivi, esaminare le etichette di ogni caso, fissare budget e retention e autorizzare il fingerprint finale. Nessun provider è stato invocato e nessuna autorizzazione umana è stata inventata.

I JSON Schema 2020-12 verificano le forme dei protocolli e delle osservazioni con envelope espliciti e campi sconosciuti rifiutati. `usage` conserva intenzionalmente chiavi native del provider come oggetto opaco; non rappresenta il contesto del catalogo. `null` rappresenta dato ignoto, distinto da zero. Il campo opzionale `reason` può spiegare l'assenza di una misura. Il campione `observation-unknown-v1.json` è illustrativo, non un risultato di esecuzione.

La validazione semantica `validate_protocol` rimane necessaria oltre allo schema: controlla membership nel corpus, unicità di ID e percorsi, disgiunzione degli split per gruppo, ordine dei budget e fingerprint dell'approvazione. Per eseguire serve `for_execution=True`; i template attuali sono intenzionalmente incompleti per tale modalità.
