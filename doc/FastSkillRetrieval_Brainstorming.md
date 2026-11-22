# FastSkillRetrieval — Brainstorming consolidato

Data: 17 ottobre 2026  
Stato: documento storico di ideazione e orientamento architetturale, non specifica esecutiva né implementazione validata.  
Ambito: recupero, preparazione e manutenzione di grandi cataloghi di skill per Claude Code e Codex.

Aggiornamento del 17 ottobre 2026: le risposte dell'utente e le scelte tecniche delegate successive a questo brainstorming sono registrate nel [§10 della proposta di soluzioni](FastSkillRetrieval_Proposta_Soluzioni.md#10-decisioni-raccolte-il-17-ottobre-2026). Le dodici domande del §16 sono chiuse come scelte di progetto per la prima versione; implementazione, prova dell'adattatore e risultati sperimentali restano da verificare. Per quelle scelte, compresa la manutenzione soltanto su comando, prevale il registro successivo; il testo sotto conserva il percorso originario.

## 1. Obiettivo e problema

Costruire un applicativo/framework che aiuti gli agenti a individuare le skill pertinenti quando il catalogo installato è numeroso, limitando il contesto aggiunto e mantenendo aggiornate descrizioni, metadati e relazioni.

Il problema riguarda sia la visibilità dei metadati sia la selezione fra skill simili. Non assumiamo una soglia universale di numero di skill oltre la quale il sistema smette di funzionare: incidono lunghezza delle descrizioni, budget, sovrapposizione degli ambiti, modello e versione del client.

La ricerca documentale svolta durante la conversazione ha confermato che Claude Code applica un budget all’elenco delle skill e può rimuovere descrizioni quando viene superato, mantenendo i nomi. Codex documenta a sua volta la selezione implicita basata sulla descrizione. I dettagli di integrazione vanno verificati sulle versioni effettivamente utilizzate. Riferimenti: [1], [3].

Il valore del framework deve essere un recupero più pertinente e controllabile, non semplicemente un elenco più grande.

## 2. Decisioni e stato delle proposte

| Punto | Stato del brainstorming |
|---|---|
| Supportare Claude Code e valutare adattamento a Codex | Obiettivo espresso |
| Preferire metodi deterministici per valutazione e retrieval | Indirizzo concordato |
| Correggere descrizioni insufficienti prima dell’ingest attivo | Decisione esplicita |
| Manutenzione schedulata o attivata da eventi | Decisione esplicita |
| Manutenere anche relazioni e collegamenti non validi | Decisione esplicita |
| Contare quante volte le skill compaiono nei risultati delle query | Decisione esplicita |
| Usare le statistiche per revisioni successive | Decisione esplicita |
| SQLite con ricerca testuale e relazioni tipizzate | Proposta architetturale |
| Hook più interfaccia MCP e adattatori separati | Proposta architetturale |
| Distinguere recupero da inserimento nel contesto | Proposta operativa |
| Metadati separati per skill di terzi, pubblicazione atomica e rollback | Proposte da confermare |
| Valutazioni empiriche della selezione del modello | Evoluzione futura, non requisito della prima fase |
| Launcher, soglie, pesi e dimensione del catalogo | Da definire |

Non sono stati scelti linguaggio, stack applicativo definitivo, soglie di qualità, frequenza di manutenzione o strategia commerciale. Non è stata attivata alcuna automazione: qui si descrive il comportamento del prodotto da realizzare.

## 3. Separazione dei due processi

Il sistema comprende due processi distinti.

**Preparazione e manutenzione del catalogo:** analizza le skill, corregge i metadati insufficienti, aggiorna l’indice e valida le relazioni. Può essere schedulato o attivato da eventi.

**Retrieval durante il lavoro:** usa esclusivamente il catalogo attivo validato per selezionare le skill utili al task corrente e ai suoi passi successivi.

Il timer richiesto dall’utente riguarda la manutenzione. Non è un timer che rilancia continuamente il retrieval durante il ragionamento.

## 4. Preparazione prima dell’ingest

### 4.1 Sequenza concordata

1. Scoprire una skill nuova o modificata.
2. Leggere il contenuto completo e i metadati originali.
3. Valutare deterministicamente la qualità della descrizione e delle informazioni di retrieval.
4. Se insufficienti, far produrre all’agente una descrizione migliorata, ancorata al funzionamento reale della skill.
5. Ripetere i controlli sulla candidata.
6. Preparare keyword, categorie e relazioni.
7. Validare la coerenza della nuova rappresentazione.
8. Pubblicare nell’indice attivo solo la versione che supera i controlli.

Una descrizione scadente non viene pubblicata nell’indice attivo in attesa di una futura correzione. Può essere conservata come sorgente o bozza di lavorazione, separata dai dati interrogati dal retrieval.

Se la validazione non riesce, la nuova versione resta da revisionare. La politica sull’eventuale versione precedente deve distinguere una semplice riscrittura fallita da una modifica del corpo della skill che rende obsoleti i vecchi metadati.

### 4.2 Ruolo del corpo della skill

L’agente deve leggere le istruzioni effettive per descriverle correttamente. Il corpo è la fonte di verifica; non è obbligatorio indicizzarlo integralmente per il retrieval.

L’idea iniziale di indicizzare anche il corpo è stata ridimensionata dopo il chiarimento: nella proposta consolidata, il catalogo di ricerca usa metadati preparati e validati. L’eventuale ricerca sul corpo resta un’opzione successiva da giustificare con risultati concreti.

### 4.3 Limiti della riscrittura

Migliorare descrizione, alias, keyword e condizioni d’uso non deve inventare capacità, modificare permessi o ampliare arbitrariamente l’ambito della skill.

La riscrittura delle istruzioni operative non è necessaria per ottimizzare il retrieval e non fa parte della proposta iniziale.

Per skill di terzi si propone un livello separato di metadati collegato all’hash della sorgente: gli aggiornamenti del fornitore non vengono sovrascritti e fanno scattare una rivalutazione. Rimane da scegliere se applicare le nuove descrizioni anche ai file nativi o mantenerle solo nel catalogo del framework.

## 5. Valutazione deterministica

### 5.1 Significato del punteggio

Per la prima fase si mantiene un punteggio deterministico di qualità dei metadati. Non è una percentuale di probabilità che Claude o Codex comprendano o selezionino correttamente la skill.

Vanno distinti:

| Misura | Significato |
|---|---|
| Qualità dei metadati | Conformità a regole esplicite di completezza e specificità |
| Pertinenza alla query | Corrispondenza fra skill e richiesta corrente |
| Frequenza di recupero | Presenza osservata della skill nei risultati delle ricerche |
| Affidabilità empirica | Selezione corretta su esempi etichettati; rinviata |

Un punteggio di qualità pari a 90/100 non deve essere presentato come “90% di confidenza del modello”.

### 5.2 Controlli candidati

- Metadati obbligatori presenti e sintatticamente validi.
- Azione, oggetto e ambito identificabili tramite campi strutturati o regole.
- Condizioni d’uso ed eventuali esclusioni esplicite.
- Tecnologie e formati riconosciuti da un vocabolario controllato.
- Lunghezza compatibile con i limiti configurati.
- Assenza di ripetizioni eccessive e keyword generiche non informative.
- Termini distintivi rispetto alle altre skill del catalogo.
- Sovrapposizione lessicale con descrizioni concorrenti.
- Riscontro dei termini tecnici nella sorgente.

La verifica lessicale non dimostra da sola la piena coerenza semantica. L’agente che corregge la descrizione deve restare ancorato al contenuto, ma nella prima fase non si introduce un benchmark obbligatorio del comportamento del modello.

Pesi, soglie e regole devono essere configurabili, versionati e spiegabili. Non sono ancora stati fissati. La qualità descrittiva non deve prevalere sulla pertinenza: una skill descritta benissimo ma non pertinente non va scelta.

## 6. Architettura proposta

| Componente | Responsabilità |
|---|---|
| Motore comune | Valutazione, indicizzazione, ricerca, ranking e budget |
| SQLite | Metadati, ricerca testuale, relazioni, versioni e statistiche |
| Adattatore Claude Code | Eventi, contesto e modalità native del client |
| Adattatore Codex | Stessi concetti tradotti nelle capacità disponibili |
| Interfaccia MCP | Ricerca e recupero espliciti su richiesta dell’agente |
| Manutentore | Scansione, correzione, reingest e revisione relazionale |
| Stato di sessione | Obiettivo, sottoattività, segnali tecnici e skill già caricate |

Gli hook attivano controlli nei punti previsti; MCP permette all’agente di chiedere skill quando riconosce un nuovo bisogno. Nessuno dei due sostituisce completamente l’altro.

Un launcher leggero è stato suggerito per configurare e avviare i client mantenendone l’interfaccia abituale, ma non è stato approvato come scelta definitiva.

## 7. Retrieval deterministico e stato del task

### 7.1 Flusso

1. Ricevere un messaggio o un evento rilevante.
2. Aggiornare lo stato del task.
3. Estrarre segnali e termini utili.
4. Eseguire ricerca testuale sui metadati validati.
5. Espandere in modo controllato le relazioni pertinenti.
6. Applicare filtri di compatibilità, dipendenze e conflitti.
7. Ordinare i candidati.
8. Selezionare fino a TOP N rispettando il budget.
9. Restituire schede candidate o caricare le skill necessarie.
10. Registrare recuperi e inserimenti senza duplicati tecnici.

TOP N è un massimo, non una quota da riempire. È valido restituire meno risultati o nessun risultato.

### 7.2 Segnali utilizzabili

- Richiesta corrente e obiettivo attivo.
- Sottoattività dichiarata dall’agente.
- File coinvolti, estensioni e manifest di progetto.
- Tecnologie e formati riconosciuti.
- Operazione imminente e risultati rilevanti dei tool.
- Errori che cambiano la natura del problema.
- Skill già caricate con identificativo e versione.

Una risposta come “sì, procedi” deve essere interpretata usando lo stato del task; le sole keyword dell’ultimo messaggio sarebbero insufficienti.

La normalizzazione può usare sinonimi italiano/inglese e vocabolari tecnici. Frasi e vincoli vanno conservati: una semplice lista di parole può perdere negazioni e condizioni come “senza modificare lo schema”.

L’LLM può essere un fallback per richieste ambigue, non una dipendenza obbligatoria a ogni query. La scelta di includerlo nella prima versione resta aperta.

## 8. SQLite e mappa relazionale

La proposta è usare FTS5 per il recupero testuale, con ranking BM25, e normali tabelle relazionali per rappresentare il grafo. SQLite supporta query ricorsive per attraversamenti. Non è necessario introdurre subito un database a grafo dedicato. Riferimenti: [5], [6].

| Relazione proposta | Esempio | Funzione |
|---|---|---|
| applies_to | skill → PostgreSQL | Compatibilità tecnologica |
| handles | skill → migrazione schema | Corrispondenza con l’attività |
| requires | skill → altra skill | Prerequisiti |
| alternative_to | skill → altra skill | Ridondanza e scelta fra alternative |
| conflicts_with | skill → altra skill | Incompatibilità |

Un generico legame di vicinanza tematica non è una ragione sufficiente per caricare altre skill. Una ricerca sulle migrazioni SQL non deve trascinare automaticamente tutte le skill sui database.

Si propone di partire da un solo livello di espansione tematica e da un limite di candidati. Le dipendenze obbligatorie richiedono una gestione distinta, con rilevamento dei cicli e verifica del costo complessivo.

Ogni relazione dovrebbe conservare origine, regola o motivazione, versioni delle entità coinvolte e data di validazione. Questo permette di rivalutare solo i collegamenti interessati da un cambiamento.

Il grafo resta uno strumento da validare: confrontare il beneficio rispetto a FTS5 e tassonomia prima di aumentarne la complessità.

## 9. Retrieval durante elaborazioni prolungate

Il recupero non deve avvenire soltanto all’inizio della conversazione. La proposta è un meccanismo guidato da eventi e cambiamenti di stato.

| Evento | Comportamento proposto |
|---|---|
| Nuovo messaggio | Aggiornare intento e selezione |
| Nuovo sotto-obiettivo | Ricalcolare le skill pertinenti |
| Lettura di manifest o file significativi | Aggiornare i segnali tecnici |
| Prima di una modifica | Verificare la copertura dell’operazione |
| Errore o risultato rilevante | Ricalcolare se cambia il problema |
| Avvio di un subagent | Preparare un insieme per il suo compito |
| Dopo compattazione | Ripristinare riferimenti necessari |

Gli adattatori devono verificare quali eventi siano realmente disponibili. Gli hook non espongono il ragionamento interno: il framework può osservare eventi e sotto-obiettivi dichiarati, non ogni cambiamento mentale dell’agente.

### 9.1 Tempistica del contesto

La documentazione di Claude chiarisce che il contesto aggiunto dagli hook viene letto nella successiva richiesta al modello. Una chiamata intercettata da PreToolUse è già stata generata: aggiungere informazioni non garantisce la revisione dell’azione imminente. Riferimento: [2].

Sono state proposte due modalità:

- Suggerimento: aggiunge contesto per i passi successivi senza interrompere.
- Copertura obbligatoria: per regole esplicite, blocca una chiamata non coperta e invita l’agente a recuperare la skill e riconsiderare l’azione.

La seconda va usata selettivamente e testata sull’adattatore. Non deve provocare interruzioni continue o loop.

Un’interfaccia concettuale come prepare_step(goal, files, operation) potrebbe favorire il recupero prima della generazione delle modifiche; gli hook fungerebbero da controllo aggiuntivo. Non è un’API già implementata.

### 9.2 Riduzione del rumore

- Non reiniettare contenuto invariato.
- Deduplicare per identificativo e versione.
- Ricalcolare solo su cambiamenti significativi.
- Escludere le operazioni del retriever dai trigger ricorsivi.
- Separare stato delle sessioni e dei subagent.
- Gestire retry e chiamate concorrenti in modo coerente.

## 10. Budget del contesto

### 10.1 Limiti distinti

1. Budget delle schede candidate: nome, descrizione, motivo e riferimento.
2. Budget dei nuovi contenuti caricati: istruzioni effettive e dipendenze.
3. Budget conservativo del materiale gestito dal framework ancora rilevante nella sessione.

Trovare cinque skill non implica caricarne integralmente cinque. Il ranking va combinato con costo in token, compatibilità e ridondanza.

### 10.2 Garanzie realistiche

Il framework può controllare ciò che emette. Nei client nativi non va presunto che possa eliminare contenuti precedenti o ricostruire esattamente tutto il prompt.

In Claude le istruzioni invocate persistono nella conversazione e sono soggette alla gestione della compattazione [1]. Codex documenta limiti approssimativi per l’output dei singoli hook, che non equivalgono a un limite globale delle skill [4].

Una garanzia rigorosa sull’intero prompt richiede controllo della sua costruzione. L’integrazione leggera deve dichiarare un budget del materiale gestito dal framework, con eventuali stime del residuo.

Contare il testo finale serializzato con tokenizer compatibile, quando disponibile. Altrimenti dichiarare una stima con margine. Non promettere precisione usando un rapporto costante caratteri/token.

Non troncare indiscriminatamente istruzioni operative per farle rientrare: preferire meno skill, caricamento mirato delle risorse o gestione esplicita del superamento.

### 10.3 Configurazione illustrativa, non approvata

```yaml
retrieval:
  max_candidates: 5
  max_graph_hops: 1
  allow_no_match: true

context:
  candidate_cards_max_tokens: 1200
  new_skill_content_max_tokens: 4000
  managed_skill_budget_tokens: 12000
  max_context_fraction: 0.08
  deduplicate_by: skill_id_and_version

maintenance:
  scan_interval_hours: 24
  rescan_changed_files: true
  evaluation_mode: deterministic
  rewrite_mode: candidate_then_validate
```

Sono esempi di parametri del futuro framework, non chiavi native dei client né valori ottimali dimostrati. La frazione di contesto richiede una dimensione di finestra nota e non garantisce da sola il controllo del prompt complessivo.

## 11. Manutenzione schedulata o attivata da eventi

### 11.1 Trigger

- Intervallo configurabile.
- Installazione o aggiornamento di skill.
- Modifica dei file sorgente.
- Eliminazione di skill.
- Comando manuale.
- Revisione completa periodica distinta dalle scansioni incrementali.

Il giro di manutenzione valuta, corregge, riesegue l’ingest e ripara il catalogo relazionale. Non si limita a riscrivere descrizioni.

### 11.2 Ciclo

1. Rilevare differenze rispetto all’ultima scansione tramite hash e inventario.
2. Identificare skill nuove, cambiate, eliminate o candidate alla revisione statistica.
3. Eseguire valutazione deterministica.
4. Far correggere all’agente i metadati insufficienti.
5. Rivalidare le candidate.
6. Aggiornare indice e metadati in area temporanea.
7. Ricostruire o rivalutare le relazioni interessate.
8. Rimuovere collegamenti morti e invalidare quelli non più giustificati.
9. Pubblicare una versione coerente del catalogo.
10. Conservare esito, versioni e possibilità di rollback.

La pubblicazione atomica è una proposta per evitare che il retrieval legga uno stato aggiornato a metà. Una manutenzione senza cambiamenti non dovrebbe provocare nuove versioni artificiali.

### 11.3 Validazione delle relazioni

| Controllo | Esempio | Approccio |
|---|---|---|
| Integrità strutturale | Destinazione eliminata | Vincoli e query deterministiche |
| Coerenza con metadati | Tecnologia non più supportata | Regole deterministiche su campi espliciti |
| Validità semantica | Due skill non sono più alternative | Rivalutazione dell’agente quando necessaria |

L’esistenza di entrambe le estremità non dimostra che una relazione sia ancora corretta. Provenienza e versionamento del legame sono essenziali.

## 12. Contatori e osservabilità

### 12.1 Decisione dell’utente

Per ora mantenere la valutazione deterministica e aggiungere un contatore per ogni skill recuperata dalle query. In seguito analizzare quelle meno recuperate per capire se conservarle, migliorarne la descrizione o correggere altre informazioni che ne influenzano la selezione.

### 12.2 Campi proposti

| Campo | Significato |
|---|---|
| retrieved_count | Numero di ricerche in cui la skill compare nei risultati TOP N |
| injected_count | Numero di volte in cui il contenuto viene inserito dopo filtri e budget |
| last_retrieved_at | Ultimo recupero |
| indexed_at | Primo ingresso nel catalogo, utile per distinguere skill recenti |

Il conteggio del recupero avviene una sola volta per coppia ricerca-skill. Un retry tecnico non deve contare come nuova domanda. Le definizioni di “ricerca effettiva”, cache hit e rinnovo della ricerca devono essere fissate nell’implementazione.

Recupero non significa utilizzo effettivo. Anche l’inserimento nel contesto non prova che l’agente abbia seguito la skill. Evitare di denominare questi contatori come esecuzioni riuscite.

### 12.3 Storico minimo proposto

Conservare identificativo della ricerca, timestamp, versione del catalogo, skill restituite, versione dei metadati, posizione, punteggio e ragione di eventuale esclusione dal budget. La conservazione del testo completo delle richieste e la relativa durata restano da decidere.

Associare statistiche alle versioni permette di confrontare il comportamento prima e dopo una riscrittura. Non azzerare senza traccia lo storico a ogni reingest: distinguere totali e dati per versione o periodo.

### 12.4 Interpretazione

Una skill poco recuperata può essere descritta male, classificata male, ridondante, nuova o semplicemente pertinente a un’attività rara. La bassa frequenza genera una candidatura alla revisione, non una cancellazione automatica.

Durante la manutenzione l’agente può verificare descrizione, keyword, categorie, raggiungibilità relazionale ed equivalenti già presenti. Le possibili azioni sono mantenere, migliorare i metadati o proporre l’archiviazione.

Le frequenze non entrano inizialmente nel ranking: favorire automaticamente le skill popolari produrrebbe un ciclo che penalizza sempre più quelle meno visibili.

## 13. Compatibilità con i meccanismi nativi

Il framework deve verificare la copertura reale degli hook e le differenze fra versioni e superfici dei client. La documentazione consultata non sostituisce prove sulle installazioni di destinazione.

Occorre decidere come evitare che il catalogo nativo completo e quello del framework vengano aggiunti entrambi al contesto. Senza questa scelta il retriever potrebbe aumentare il consumo invece di ridurlo.

Leggere un file SKILL.md attraverso MCP non equivale necessariamente a invocarlo nativamente: permessi, risorse relative, script, hook e altre proprietà potrebbero richiedere una gestione specifica. L’adattatore deve preservare queste caratteristiche o dichiarare esplicitamente cosa supporta.

## 14. Modello dati concettuale proposto

Non è uno schema SQL definitivo, ma una mappa delle informazioni da conservare.

| Entità | Contenuto |
|---|---|
| Skill | Identità stabile, origine, percorso, stato |
| Versione sorgente | Hash e riferimenti al contenuto |
| Versione metadati | Descrizione validata, keyword, categorie, regole e punteggio |
| Concetto | Tecnologia, attività, formato o categoria |
| Relazione | Estremità, tipo, provenienza e validazione |
| Ricerca | Evento, versione catalogo e parametri |
| Risultato ricerca | Skill, versione, posizione e punteggio |
| Inserimento | Skill/versione, sessione e costo del contenuto emesso |
| Esecuzione manutenzione | Trigger, cambiamenti, esiti e pubblicazione |

## 15. Evoluzioni rinviate

Sono state discusse, ma non diventano requisiti della prima fase:

- Benchmark con richieste positive, negative e ambigue.
- Recall@K, precisione e attivazioni inappropriate su esempi etichettati.
- Confronto fra recupero corretto e scelta effettiva di Claude/Codex.
- Verifica empirica delle riscritture su un insieme separato da quello usato per ottimizzarle.
- Ricerca semantica o fallback LLM più articolato.
- Indicizzazione opzionale del corpo delle skill.

Queste evoluzioni non sostituiscono la scelta attuale: controlli deterministici più contatori e storico per orientare la manutenzione.

## 16. Domande aperte al momento del brainstorming

Per lo stato successivo consultare il registro delle decisioni indicato in apertura; questa lista conserva le domande originali e non dichiara ancora aperte le risposte già acquisite.

1. L’interazione deve restare direttamente nelle CLI oppure è accettabile un launcher leggero?
2. Il budget richiesto riguarda il materiale del framework o l’intero prompt di ogni chiamata?
3. Quante skill sono previste: decine, centinaia o migliaia?
4. Il catalogo è personale, di terzi o misto?
5. Le descrizioni corrette sostituiscono i file originali o restano in metadati separati?
6. Quali soglie deterministiche e quali limiti ai tentativi di correzione adottare?
7. Con quale frequenza eseguire manutenzione incrementale e revisione completa?
8. Come trattare una skill modificata la cui nuova descrizione non supera i controlli?
9. Quando una skill può essere archiviata e con quale livello di automazione?
10. Quali dati delle ricerche conservare e per quanto tempo?
11. Quali operazioni richiedono copertura obbligatoria prima dell’esecuzione?
12. Quali versioni e superfici di Claude Code e Codex supportare inizialmente?

## 17. Direzione iniziale proposta

Realizzare il nucleo minimo composto da preparazione pre-ingest, valutazione deterministica, catalogo SQLite, ricerca testuale, metadati strutturati e contatore dei recuperi. Integrare il recupero durante il lavoro attraverso adattatori e hook, con budget dichiarato e deduplicazione.

Affiancare manutenzione incrementale, reingest e validazione delle relazioni. Introdurre complessità nel grafo solo dove offre un beneficio verificabile rispetto alla ricerca testuale e alla tassonomia.

Il documento consolida il brainstorming, comprese le correzioni dell’utente. Non afferma che siano già stati realizzati, collaudati o autorizzati deployment, scheduler o modifiche alle skill installate.

## 18. Fonti consultate durante il brainstorming

Le fonti seguenti supportano i comportamenti dei prodotti e le capacità di SQLite; l’architettura del framework, i parametri e le politiche sono proposte progettuali. Consultazione effettuata nella conversazione del 17 ottobre 2026; non è stata ripetuta una verifica delle versioni locali durante la redazione di questo file.

1. [Claude Code — Skills](https://code.claude.com/docs/en/skills): selezione, budget delle descrizioni e ciclo di vita delle istruzioni.
2. [Claude Code — Hooks reference](https://code.claude.com/docs/en/hooks): eventi e tempistica del contesto aggiunto.
3. [OpenAI — Build skills](https://learn.chatgpt.com/docs/build-skills): selezione esplicita e implicita; pagina raggiunta dalla documentazione Codex.
4. [OpenAI — Hooks](https://learn.chatgpt.com/docs/hooks): eventi, copertura e limiti degli output; pagina raggiunta dalla documentazione Codex.
5. [SQLite — FTS5](https://www.sqlite.org/fts5.html): ricerca testuale e ranking BM25.
6. [SQLite — WITH clause](https://www.sqlite.org/lang_with.html): query ricorsive e attraversamento di relazioni.
