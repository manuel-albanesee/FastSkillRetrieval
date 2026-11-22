# FastSkillRetrieval — Proposta di soluzione e contratto iniziale

Data: 17 ottobre 2026. Stato: progetto con decisioni confermate e scelte tecniche delegate registrate nel §10; le altre soluzioni restano proposte. È stata aggiunta una prima implementazione locale in modalità assistenza: perimetro, prove effettive e limiti sono registrati in `Prima_Implementazione.md`. La verifica nel client Claude Code e la prova del catalogo gestito restano da completare.

Fonti di progetto: `FastSkillRetrieval_Brainstorming.md` e `FastSkillRetrieval_Revisione_ECC.md`.

Metodo ECC: Product Capability per vincoli, stati e interfacce; applicazione dei criteri di Eval Harness e Agent Security Hardening già letti durante la revisione. Le interfacce descritte qui sono del prodotto proposto, non API native già disponibili in Claude Code o Codex. Non vengono creati ADR di decisioni accettate.

## 1. Capacità e risultato atteso

L'utilizzatore iniziale confermato è uno sviluppatore che lavora con Claude Code e possiede un catalogo personale misto, con skill proprie e di terzi. Il prodotto deve preparare un catalogo verificabile, recuperare skill pertinenti al lavoro corrente e renderle utilizzabili senza nascondere esclusioni, guasti o costi. L'obiettivo di scala è fino a 5.000 skill; il primo catalogo di prova comprende 100 skill rappresentative. Questi numeri definiscono la prova da realizzare, non prestazioni già dimostrate.

Il primo adattatore usa direttamente Claude Code CLI, senza launcher. Il 17 ottobre 2026 è stata rilevata localmente la versione 2.1.302 su Windows/PowerShell: è il bersaglio della prima prova di integrazione, non una versione già certificata compatibile. Codex è rinviato a una fase successiva.

Il primo risultato osservabile è questo: l'utilizzatore completa inventario e preparazione, vede le esclusioni con motivazione, esegue una ricerca e carica una skill con risorse coerenti. Il risparmio complessivo di contesto è una promessa condizionata alla verifica dell'adattatore.

## 2. Vincoli da conservare e nuove preferenze proposte

### Indirizzi già presenti nel brainstorming

- Preparazione e retrieval sono processi distinti.
- Controlli deterministici e correzione delle descrizioni insufficienti prima dell'ingest attivo.
- Retrieval deterministico sui metadati validati.
- Manutenzione incrementale e revisione delle relazioni.
- Contatori dei recuperi e storico; la popolarità non entra inizialmente nel ranking.
- Il benchmark del comportamento del modello rimane rinviato.

### Scelte confermate dall'utente

- Uso diretto di Claude Code CLI senza launcher; Codex successivo.
- Catalogo personale misto, fino a 5.000 skill; prova iniziale su 100.
- Metadati migliorati separati dagli originali.
- Budget riferito al solo materiale aggiunto dal framework.
- Valori iniziali configurabili: massimo 5 candidati, 1.200 token per le schede, 4.000 token di nuove istruzioni per caricamento e 12.000 token di materiale gestito per periodo di contesto; istruzioni non troncate per rientrare.
- Prima versione in modalità suggerimento, senza operazioni soggette a copertura obbligatoria.
- Claude Code nella sessione attiva genera le candidate usando i tool locali: nessun provider LLM separato invocato dal prodotto.
- Manutenzione soltanto su comando dell'utente; nessuna scansione automatica o schedulazione nella prima versione.
- Massimo due tentativi per skill e configurazione pertinente, una candidata alla volta; politica delle versioni precedenti descritta in S03.
- Sovrapposizione fra skill segnalata come avviso, senza blocco automatico.
- Statistiche come segnale di revisione; archiviazione soltanto su scelta dell'utente, conservando sorgenti e storico.
- Log locali senza testo delle richieste o contenuto dei file: massimo 30 giorni e 100 MB, esportazione esplicita e filtrata.
- Prova di utilità su 100 skill e 20 richieste reali concordate; nessuna perdita sui richiami essenziali concordati, riduzione obiettivo del materiale di catalogo esposto di almeno il 30% rispetto ai soli metadati migliorati e latenza locale a regime p95 non superiore a 300 ms, da verificare successivamente su 5.000 skill.

### Scelte tecniche e altre preferenze architetturali

- Applicazione locale con un'unica base di codice e moduli separati; nessuna necessità iniziale di microservizi.
- SQLite come archivio locale; una sola pubblicazione del catalogo alla volta.
- Versioni immutabili del catalogo e dei pacchetti ammessi, con verifica all'uso.
- CLI per inventario, manutenzione, ricerca e diagnostica; interfaccia MCP sottile per l'agente.

La manutenzione schedulata resta una possibile evoluzione: la scelta successiva dell'utente di eseguire manutenzione soltanto su comando prevale sull'indirizzo iniziale del brainstorming. Una futura modalità obbligatoria richiederebbe una nuova decisione e prove del percorso interessato.

## 3. Architettura concreta proposta

Tre moduli nello stesso prodotto:

1. **Preparatore:** su comando inventaria, legge le sorgenti e valida; espone a Claude Code nella sessione attiva i dati necessari a proporre riscritture, riceve candidate e produce una versione candidata del catalogo. Non avvia autonomamente un modello.
2. **Catalogo e retriever:** mantiene identità, versioni, metadati, relazioni e statistiche; risponde a query strutturate senza chiamate LLM obbligatorie.
3. **Adattatore:** osserva gli eventi supportati, conserva lo stato di sessione, espone ricerca e caricamento e applica soltanto le garanzie verificate sul client.

Il preparatore può essere lento o indisponibile senza trasformare il retrieval in un ciclo di riscrittura in tempo reale. Una sorgente nuova non pronta resta visibile nell'inventario, ma fuori dal catalogo interrogabile.

## 4. Soluzioni ai dodici rilievi

### S01 — Separare il valore dei metadati dal valore del retriever

**Risolve:** rilievo 1.

Predisporre tre configurazioni reversibili:

- A: catalogo nativo attuale.
- B: metadati migliorati resi visibili attraverso un ambiente di prova, se il client lo consente senza alterare l'installazione ordinaria.
- C: gli stessi metadati di B, con retrieval e adattatore.

Registrare preparazione, manutenzione, materiale emesso, latenza e interventi richiesti. Se B non può essere riprodotta fedelmente sul client, documentare il limite: un confronto offline non dimostra lo stesso comportamento nativo.

La prima prova verifica installazione e funzionamento. L'utente ha confermato una successiva prova manuale di utilità su 100 skill e 20 richieste reali concordate, con i criteri quantitativi del §10.6. Non è un benchmark generale del comportamento LLM né una dimostrazione statistica. Finché manca una valutazione pertinente, dichiarare non dimostrato il miglioramento generale della selezione.

**Esito decisionale:** se B offre già il beneficio sufficiente, rilasciare prima il preparatore. Se C aggiunge valore osservabile sostenibile, proseguire con il retriever durante il lavoro.

### S02 — Rendere esplicita la modalità di integrazione

**Risolve:** rilievo 2.

Ogni adattatore espone un rapporto delle capacità con client, versione e superficie provati. Stati proposti:

| Modalità | Comportamento | Promessa consentita |
|---|---|---|
| Osservazione | Inventario e diagnostica, nessun contenuto aggiunto al task | Misura della situazione iniziale |
| Assistenza | Ricerca e suggerimenti; possibile coesistenza del catalogo nativo | Utilità dei recuperi, overhead dichiarato |
| Catalogo gestito | Punto d'ingresso sempre disponibile e esposizione controllata delle skill gestite | Riduzione misurata del catalogo esposto, nei limiti provati |

La terza modalità è attivabile soltanto dopo aver verificato visibilità, attivazione, invocazioni esplicite, risorse e ripristino. Non si presume che basti disabilitare una skill nativa e leggerne poi il corpo via MCP: potrebbe violare le condizioni d'uso o perdere proprietà necessarie.

Il punto d'ingresso deve spiegare quando cercare una skill e restare disponibile anche quando il catalogo esteso è nascosto. Una richiesta esplicita dell'utente ha precedenza sul ranking, ma resta soggetta a disponibilità e permessi.

Conservare una configurazione precedente per il ripristino. Una modalità non supportata produce `unsupported_integration`; nessun adattamento silenzioso che aggiri blocchi del client.

**Accettazione:** una skill con risorsa relativa è utilizzabile nel client scelto; il rapporto distingue ciò che è preservato, stimato e non supportato.

### S03 — Inventario completo separato dal catalogo attivo

**Risolve:** rilievo 3.

Introdurre stati per ciascuna versione sorgente:

`discovered -> preparing -> validated -> published`

Percorsi alternativi: `preparing -> needs_revision`, `validated -> blocked`, `published -> superseded`, qualsiasi stato pertinente verso `withdrawn`.

Conservare separatamente motivo di esclusione, ultimo tentativo, numero di tentativi e prossima azione. Un fallimento non viene nascosto sotto un unico contatore di recuperi pari a zero.

Politica confermata per la versione precedente:

- Fallisce soltanto una nuova riscrittura di metadati, con sorgente invariata: mantenere la versione pubblicata già valida.
- Cambia il pacchetto sorgente: non riassociare i vecchi metadati al nuovo contenuto. La nuova versione resta esclusa finché non è valida.
- La vecchia versione può restare utilizzabile soltanto se esiste un pacchetto immutabile conservato, ancora abilitato e non ritirato. In assenza di queste condizioni, escludere la skill finché non è pronta.

La diagnostica delle escluse è accessibile all'operatore, separata dai risultati dell'agente.

**Accettazione:** una skill esclusa è trovabile nell'inventario con una spiegazione, ma non compare nel retrieval attivo.

### S04 — Separare preparazione, fiducia e permessi

**Risolve:** rilievo 4.

Claude Code, nella sessione attiva, riceve attraverso i tool un insieme limitato di sorgenti e restituisce una candidata conforme a uno schema. Il server locale non avvia un modello, non esegue script delle skill e non riceve credenziali di un provider LLM. I tool di preparazione consentono soltanto letture nell'inventario autorizzato e scritture nei metadati derivati, attraverso i propri validatori.

Campi ammessi proposti: descrizione, alias, capacità, condizioni d'uso, esclusioni, concetti ed evidenze nella sorgente. Le evidenze legano le affermazioni a versione e passaggi specifici; aiutano la revisione, senza dimostrare da sole equivalenza semantica.

Identità, permessi, abilitazione e fiducia sono impostati fuori dall'output generato. Un contenuto che richiede di cambiare queste proprietà non riceve autorità attraverso la riscrittura.

I limiti vanno applicati realmente al confine di ogni tool: percorsi contenuti nelle radici autorizzate, campi ammessi, dimensioni finite, versione sorgente verificata e pubblicazione separata dalla proposta. Un prompt che dice «non eseguire» non basta. La candidata viene validata prima di ogni pubblicazione.

Questa architettura non isola l'intera sessione Claude Code: altri strumenti del client restano soggetti ai suoi permessi e alle sue protezioni. Il corpo della skill resta dato non attendibile e il superamento della validazione dei metadati non certifica l'assenza di prompt injection. Un requisito di isolamento del generatore nel suo complesso richiederebbe un'architettura distinta e una prova specifica.

Il trasporto MCP locale non rende locale l'inferenza di Claude Code: i contenuti letti nella sessione seguono la configurazione e le condizioni del client. Il framework non effettua invii aggiuntivi a provider esterni. La prova iniziale deve verificare il percorso MCP documentato, i permessi e le condizioni applicabili all'installazione effettiva, senza riutilizzare credenziali del client in un servizio separato.

**Accettazione:** passando dai veri ingressi dei tool, una sorgente ostile o una candidata con campi, percorsi o dimensioni non ammessi non causa esecuzioni o scritture fuori contratto. I casi semanticamente dubbi restano `needs_revision`. Non presentare questa verifica come prova di isolamento dell'intera sessione Claude Code.

### S05 — Ricerca e caricamento sulla stessa versione

**Risolve:** rilievo 5.

Ogni pacchetto ha un manifesto dei file pertinenti: `SKILL.md`, risorse locali e script dichiarati. La preparazione calcola gli hash dei file e del manifesto. Una dipendenza remota o esterna non congelabile rimane esplicita e limita la garanzia di riproducibilità.

Proporrei copie immutabili delle risorse ammesse in una cache locale del prodotto, senza modificare i file del fornitore. I confini del pacchetto sono configurati: niente copia indiscriminata di directory superiori, credenziali, collegamenti esterni o file estranei. Prima dell'esecuzione l'adattatore verifica anche eventuali requisiti del percorso originale; se la copia cambia il comportamento, dichiara quella skill non supportata dalla modalità di snapshot.

La ricerca restituisce un riferimento a skill, pacchetto, metadati e catalogo. Il caricamento usa quel riferimento e verifica che sia ancora valido e utilizzabile. Se non può garantire coerenza restituisce `stale_reference`, chiedendo un nuovo recupero.

**Accettazione:** una modifica intervenuta fra ricerca e caricamento non porta a una combinazione silenziosa di vecchi metadati e nuovi script.

### S06 — Bloccare la versione per la durata della sessione

**Risolve:** rilievo 6.

La prima ammissione di una skill alla sessione lega quella sessione alla versione del pacchetto. Le nuove sessioni possono usare gli aggiornamenti; quelle esistenti continuano con la versione precedente finché valida e disponibile.

Non aggiornare automaticamente una skill già caricata. Una sostituzione richiesta dall'utente produce un avviso di incompatibilità se necessario; per modifiche incompatibili proporre una nuova sessione. Una versione ritirata per motivi di fiducia viene bloccata per i caricamenti successivi: il sistema non può cancellare retroattivamente il testo già emesso.

Stato minimo: obiettivo attivo e revisione, vincoli espliciti dell'utente, versioni scelte, contenuti emessi e periodo di contesto. Dopo compattazione si apre un nuovo periodo; il sistema non presume che un vecchio evento di emissione provi la presenza attuale delle istruzioni. Il ripristino è limitato al task ancora attivo e al budget disponibile.

**Accettazione:** pubblicare v2 non causa caricamenti automatici di v2 in una sessione che usa v1. La ripresa non trasforma contenuti superati in vincoli correnti.

### S07 — Esiti tipizzati e modalità obbligatoria condizionata

**Risolve:** rilievo 7.

Separare almeno questi esiti:

| Esito | Significato | Recupero |
|---|---|---|
| `ok` | Risultati utilizzabili, anche meno del massimo | Continuare |
| `no_match` | Ricerca completata, nessuna corrispondenza utilizzabile | Continuare senza skill se consentito |
| `catalog_not_ready` | Nessun catalogo attivo disponibile | Preparare o ripristinare |
| `catalog_unavailable` | Errore di accesso al catalogo | Diagnostica e retry limitato |
| `dependency_unavailable` | Skill pertinente ma prerequisiti non risolti | Mostrare il prerequisito mancante |
| `budget_exceeded` | Il pacchetto necessario non entra nel budget | Ridurre il piano o richiedere una scelta |
| `stale_reference` | Versione cambiata o riferimento non più utilizzabile | Nuova ricerca |
| `policy_denied` | Ambito, fiducia o permessi impediscono l'uso | Esplicitare il vincolo |
| `unsupported_integration` | Il client non preserva il comportamento richiesto | Cambiare modalità o superficie |
| `timeout` | Una chiamata locale supera il proprio tempo massimo | Diagnostica e ripresa esplicita, senza successo simulato |
| `limit_exceeded` | Volume o dimensione supera un limite operativo distinto dal budget di contesto | Ridurre il lotto o richiedere una configurazione esplicita |

Per risposte miste, `ok` comprende risultati utilizzabili e ragioni di esclusione per gli altri candidati. Se nessun risultato è utilizzabile, classificare il motivo; non trasformare tutti i casi in `no_match`.

In modalità suggerimento, un guasto viene segnalato e il client può proseguire secondo le sue regole. La copertura obbligatoria è ammessa soltanto dove il confine che esegue l'azione può verificarla e applicarla realmente. Se il percorso non è controllabile, il prodotto dichiara la garanzia non supportata.

Un'attestazione di copertura, quando supportata, riguarda sessione, operazione concreta, risorse interessate, versioni e validità temporale. Una lettura generica di una skill non certifica tutte le operazioni successive. La copertura non concede autorizzazioni e non dimostra che l'agente seguirà le istruzioni.

**Accettazione:** guasti, risultati vuoti e blocchi hanno esiti distinti. Un timeout non viene contabilizzato come ricerca riuscita.

### S08 — Identità composta e isolamento per progetto

**Risolve:** rilievo 8.

Assegnare un ID interno stabile all'installazione/origine e mantenere nome pubblico, namespace, origine e ambito come campi separati. Le copie e i fork sono identità distinte con una possibile relazione di equivalenza: non vengono fusi automaticamente per nome o hash.

Il retrieval applica prima l'ambito autorizzato del progetto e le abilitazioni, poi le preferenze configurate fra fonti, infine il ranking di pertinenza. Una priorità di progetto non deve sostituire un vincolo amministrativo o una disabilitazione esplicita.

Ogni risultato mostra origine e ambito. Le dipendenze fanno riferimento a identità o a requisiti dichiarati, non a nomi ambigui. I metadati di un progetto riservato non devono essere emessi nel contesto di un altro progetto.

**Accettazione:** due skill omonime rimangono distinguibili e una disabilitazione non viene aggirata dal caricamento MCP.

### S09 — Separare validazione locale e confronto globale

**Risolve:** rilievo 9.

Usare due gruppi di controlli:

- Locali: schema, campi richiesti, lunghezza, evidenze e vincoli del singolo pacchetto. La validità dipende da sorgente e regole versionate.
- Globali: sovrapposizione, distintività e alternative nel catalogo candidato. Dipendono anche dall'insieme delle skill.

Il passaggio pre-ingest continua a richiedere i controlli concordati. Per scelta confermata, la sola sovrapposizione globale genera una segnalazione e una proposta di disambiguazione, senza blocco automatico o riscritture ricorsive illimitate. Trasformarla in un blocco di ammissione richiederebbe una nuova decisione.

Per la prima versione ricalcolare i confronti globali sul catalogo candidato completo; ottimizzare per gruppi solo dopo aver verificato il costo. Congelare il catalogo di confronto durante una generazione di manutenzione, così le candidate non inseguono continuamente un bersaglio che cambia.

La pubblicazione registra versione di normalizzazione, vocabolario, regole, ranking e pacchetti. A parità di query strutturata, configurazione e snapshot, l'ordinamento è stabile, con criterio esplicito per i pareggi. La riproducibilità del retrieval non implica che una nuova generazione LLM riproduca gli stessi metadati.

**Accettazione:** aggiungere una concorrente fa scattare la rivalutazione globale prevista senza riscrivere automaticamente l'intero catalogo in un ciclo.

### S10 — Query strutturata con vincoli separati dal testo

**Risolve:** rilievo 10.

La query interna contiene obiettivo, operazione dichiarata, tecnologie, versioni, file pertinenti, esclusioni e ambito di sessione. Il motore conserva separatamente termini esatti, alias e vincoli. `C++` e `C#` hanno identificatori distinti; non dipendono soltanto da come la punteggiatura viene spezzata nella ricerca.

Usare vocabolari e mapping versionati per italiano/inglese. Separare condizioni positive e controindicazioni dei metadati. Costruire l'espressione FTS da termini validati e correttamente trattati; non inoltrare il messaggio grezzo come sintassi della query.

L'estrazione deterministica non garantisce comprensione di tutte le negazioni. Per vincoli non interpretabili con affidabilità, mantenere il testo originale come vincolo non risolto e produrre una richiesta di chiarimento o suggerimenti prudenti; evitare esclusioni rigide basate su euristiche fragili.

Gli stati come «sì, procedi» aggiornano l'obiettivo già attivo. Un nuovo obiettivo esplicito o una cancellazione ne cambia la revisione e invalida le ricerche dipendenti dallo stato precedente.

**Accettazione:** casi dedicati distinguono tecnologie confondibili, esclusioni e cambio di obiettivo senza richiedere un LLM a ogni ricerca.

### S11 — Limiti operativi e manutenzione idempotente

**Risolve:** rilievo 11.

Impostazioni iniziali confermate: una sola candidata alla volta e al massimo due tentativi per skill e configurazione pertinente. Esauriti i tentativi, la skill resta `needs_revision`; un semplice timer non azzera il contatore. Nuovo tentativo dopo cambiamento pertinente o richiesta esplicita dell'operatore.

La prima versione non avvia chiamate LLM autonome: non richiede un budget API separato per il framework. Il consumo della sessione Claude Code segue il client; il prodotto non promette né costo nullo né un tetto economico sulla sessione. Restano necessari limiti finiti per tempo dei tool, volume delle sorgenti e candidate, numero di tentativi e lavoro accettato per esecuzione. Un futuro generatore esterno o uno scheduler con inferenza autonoma richiederebbe una nuova scelta su provider, riservatezza e budget.

L'unità di cache include pacchetto, regole e configurazione del generatore. Una sola manutenzione pubblica alla volta; le query leggono un catalogo coerente. Spazio insufficiente, lock o arresto lasciano attivo l'ultimo catalogo integro, con esclusione delle sorgenti esplicitamente ritirate.

Rollback significa ripristino dell'indice/configurazione, non annullamento dei contenuti già emessi né riabilitazione di versioni ritirate.

La manutenzione avviene soltanto su comando dell'utente. Non viene avviata automaticamente all'inizio della sessione o al cambiamento di una sorgente. I controlli d'integrità al caricamento restano obbligatori: un riferimento non più valido produce un esito esplicito, senza far partire manutenzione o riscrittura implicita.

**Accettazione:** manutenzione ripetuta senza cambiamenti non produce nuove versioni; fallimenti non generano spesa o retry illimitati; una pubblicazione incompleta non diventa attiva.

### S12 — Misurare disponibilità, esposizione e guasti separatamente

**Risolve:** rilievo 12.

Mantenere i contatori richiesti e aggiungere il contesto necessario a interpretarli:

- skill scoperte, pubblicate ed escluse, con durata di effettiva disponibilità;
- ricerche riuscite, senza corrispondenza, fallite e bloccate;
- recuperi e contenuti emessi per versione, modalità, progetto autorizzato e configurazione;
- esclusioni per compatibilità, dipendenza, politica e budget;
- tempo di preparazione, manutenzione, ricerca e caricamento, separati;
- token osservati o stimati, con metodo di stima dichiarato.

Le frequenze normalizzate usano denominatori espliciti e periodi confrontabili. Non chiamare una frequenza «probabilità di essere utile». Un caricamento non è prova di applicazione della skill.

Per la prima versione sono confermati log strutturati senza testo integrale delle richieste né contenuti dei file, limitati a ID locali, versioni, tempi ed esiti. Anche nomi, percorsi e identificativi possono essere sensibili: i log restano locali e l'esportazione è esplicita e filtrata. Conservazione massima di 30 giorni e spazio massimo di 100 MB per i log persistenti, incluse rotazioni e diagnostica: eliminare prima i più vecchi al raggiungimento di uno dei limiti. La diagnostica dettagliata non può aggirare i divieti sui contenuti; un suo ampliamento richiede una scelta separata. I contatori aggregati per skill/versione e lo storico delle decisioni di catalogo sono distinti dai log delle singole ricerche.

Le statistiche possono suggerire la revisione di una skill poco recuperata, senza dimostrarne l'inutilità. L'archiviazione richiede una scelta esplicita dell'utente: rimuove la skill dal catalogo attivo in modo reversibile e conserva sorgenti e storico. Una bassa frequenza non avvia automaticamente né archiviazione né manutenzione.

**Accettazione:** aumentando soltanto il volume delle query il rapporto non attribuisce automaticamente il cambiamento a una descrizione migliore.

## 5. Contratto delle interfacce proposte

### Preparazione

`prepare_catalog(scope, source_inventory, policy, generation_limits)`

Produce ID dell'esecuzione, catalogo candidato, ammissioni, esclusioni e costi. Non modifica il catalogo attivo fino a pubblicazione completa. Una candidata valida deve superare anche disponibilità delle risorse e politica di abilitazione.

### Ricerca

`search_skills(request_id, session_id, task_revision, query, budget, catalog_ref?)`

Produce esito, riferimento del catalogo, candidati ordinati, versioni, motivazioni, costi previsti e ragioni di esclusione. I risultati contano una sola volta per richiesta effettiva e skill. Lo stesso `request_id` con input diverso viene rifiutato; un retry tecnico non costituisce una nuova domanda.

La ricerca filtra l'ambito prima del ranking, risolve conflitti e calcola il costo delle dipendenze obbligatorie prima della selezione finale. Il pacchetto selezionato include le dipendenze necessarie oppure viene escluso con una ragione. Un ciclo obbligatorio irrisolvibile produce `dependency_unavailable` con diagnostica; non causa espansione infinita.

La prima versione può limitare il grafo alle relazioni necessarie per applicabilità, dipendenze, alternative e conflitti. Espansioni tematiche ulteriori restano misurabili separatamente.

### Preparazione del caricamento

`prepare_skill_use(session_id, candidate_ref, operation, resource_scope)`

Verifica integrità, versione della sessione, ambito, abilitazione, disponibilità, budget e capacità dell'adattatore. Produce un piano di caricamento o un esito negativo tipizzato. Non esegue gli script della skill né concede permessi.

### Caricamento ed eventi

`load_skill(load_plan_ref)`

L'adattatore realizza il piano usando il percorso supportato e registra ciò che osserva. Dove il client non offre una conferma affidabile, registra «contenuto emesso», non «skill applicata». Il confine dell'eventuale azione successiva rivalida autorizzazione e copertura, dove supportato, evitando di fidarsi di una verifica passata se le condizioni sono cambiate.

Queste interfacce sono concettuali e possono diventare funzioni locali, comandi o strumenti MCP. Lo stack iniziale è scelto nel §10; gli schemi eseguibili e i nomi definitivi dei tool saranno fissati nell'implementazione.

## 6. Dati minimi e responsabilità

| Entità | Scopo |
|---|---|
| Origine/installazione | Identità, provenienza, namespace, ambito, abilitazione |
| Pacchetto | Manifesto dei file, hash, risorse, versione e stato di ritiro |
| Metadati | Candidata, evidenze, regole, qualità e motivazione delle esclusioni |
| Catalogo | Pubblicazione coerente, configurazioni versionate e dipendenze |
| Sessione | Revisione del task, vincoli, versioni scelte e periodo di contesto |
| Ricerca/risultati | Idempotenza, posizioni, motivazioni e stato osservato |
| Emissione/caricamento | Contenuto emesso, riscontro disponibile e costo |
| Manutenzione | Tentativi, limiti, esito e pubblicazione |

Le sorgenti del fornitore restano la fonte originale. Il catalogo conserva una rappresentazione derivata e riferita a versioni precise; una riscrittura non diventa una nuova verità sul contenuto per il solo fatto di essere stata generata.

## 7. Ordine di realizzazione e verifiche

### Passo 1 — Prova dell'adattatore

Prima attività proposta: verificare su Claude Code CLI visibilità del catalogo, punto d'ingresso, caricamento di risorse, invocazioni esplicite, eventi osservabili e ripristino. Includere una prova di errore e timeout se si valuta la copertura obbligatoria.

**Criterio di uscita:** rapporto riproducibile delle modalità supportate. Se il controllo del catalogo fallisce, mantenere soltanto osservazione/assistenza o cambiare approccio; non promettere risparmio globale di contesto.

### Passo 2 — Inventario e preparazione

Implementare identità, stati delle escluse, generazione limitata, metadati separati e versione dei pacchetti. Pubblicazione completa e rollback prima dello scheduler.

**Criterio di uscita:** passano S03, S04, S05 e la parte di S11 relativa a pubblicazione e retry.

### Passo 3 — Retriever minimo e stato di sessione

Implementare ricerca testuale, query strutturata, filtri di ambito, dipendenze, budget, esiti e contatori. Collegare il caricamento all'adattatore verificato e alla versione della sessione.

**Criterio di uscita:** passano i casi S06–S10 e S12. Stesso snapshot/configurazione/input produce lo stesso ordinamento.

### Passo 4 — Prova di utilità e manutenzione manuale

Eseguire il confronto S01 nei limiti concordati; decidere se il retriever aggiunge valore. Misurare manutenzione e overhead sul percorso manuale e verificare la scala obiettivo di 5.000 skill dopo la prova su 100. Codex resta successivo; lo scheduler non appartiene alla prima versione e richiederebbe una nuova decisione.

**Criterio di uscita:** rapporto sui criteri confermati nel §10.6, con beneficio osservato, eventuali fallimenti e limiti della prova. Gli obiettivi numerici sono stati fissati prima della misurazione; non sono risultati già ottenuti.

Le verifiche qui descritte sono criteri di accettazione da implementare, non test già eseguiti. L'uso della metodologia ECC Eval Harness non implica esecuzione dei suoi strumenti sperimentali né certificazione dell'isolamento del prodotto.

## 8. Ambito escluso dalla prima realizzazione

- Controllo o cancellazione dell'intero prompt del client.
- Garanzia che il modello segua le istruzioni soltanto perché sono state caricate.
- Riscrittura delle istruzioni operative dei fornitori.
- Ricerca semantica o chiamata LLM obbligatoria a ogni query.
- Espansione tematica complessa del grafo prima del percorso base.
- Servizio condiviso fra più utenti o progetti con isolamento remoto.
- Parità dichiarata fra client senza prove dei rispettivi adattatori.

## 9. Decisioni raccolte e passaggio all'esecuzione

**Pronto per implementazione completa:** no; il controllo sul catalogo nativo e la preservazione dell'invocazione richiedono la prova del passo 1.

**Prossima attività concreta:** prova tecnica dell'adattatore su ambiente isolato, con inventario e configurazione ripristinabile. Non richiede di costruire prima grafo, scheduler e preparazione dell'intero catalogo.

Linguaggio, packaging, regole iniziali di qualità/ranking e limiti operativi sono stati scelti su delega; client iniziale, scala, modalità di generazione, manutenzione, archiviazione, log, budget di contesto e criteri sperimentali sono registrati nel §10. Le dodici domande iniziali hanno ora una scelta di progetto o un rinvio esplicito per la prima versione, come riepilogato nel §10.7; non rimangono risposte dell'utente in sospeso per quel perimetro. Gli schemi e i controlli eseguibili, il blocco delle versioni delle dipendenze e i test dell'adattatore e dell'intero percorso restano lavoro d'implementazione e verifica.

Soltanto le scelte esplicitamente confermate o selezionate su delega nel §10 sono decisioni acquisite; questo non approva automaticamente tutte le altre proposte del documento. I passi esecutivi successivi possono usare ECC TDD Workflow per i comportamenti critici ed ECC Verification Loop per il percorso completo, adattando le prove alla superficie realmente scelta.

## 10. Decisioni raccolte il 17 ottobre 2026

Questa sezione registra le risposte dell'utente successive alla revisione. Le formulazioni storiche ancora propositive nei documenti precedenti non riaprono le scelte qui confermate. Una decisione presa non costituisce evidenza di implementazione o collaudo.

### 10.1 Decisioni dell'utente

| Tema | Decisione | Provenienza |
|---|---|---|
| Client e interazione | Claude Code CLI direttamente, senza launcher; Codex successivo | Prima serie di risposte |
| Catalogo | Personale misto; obiettivo fino a 5.000 skill, prova iniziale su 100 | Prima serie di risposte |
| Metadati | Separati dagli originali | Prima serie di risposte |
| Contesto | Solo materiale del framework; massimo 5 candidati, 1.200 token schede, 4.000 token nuove istruzioni per caricamento, 12.000 token per periodo; valori iniziali configurabili | Prima e quarta serie di risposte |
| Modalità | Suggerimento; nessuna operazione richiede copertura obbligatoria nella prima versione | Prima serie di risposte |
| Scelte tecniche | Stack, packaging e dettagli tecnici delegati all'assistente, con motivazioni documentate | Prima serie di risposte |
| Generazione | Claude Code nella sessione attiva usa i tool; nessun provider autonomo del framework | Seconda serie di risposte e chiarimento sul costo |
| Log | Locali, senza testi delle richieste o contenuti dei file; massimo 30 giorni e 100 MB; esportazione esplicita e filtrata | Seconda serie di risposte |
| Manutenzione | Soltanto su comando dell'utente | Terza serie di risposte; aggiorna l'indirizzo precedente su timer/eventi |
| Archiviazione | Soltanto su scelta dell'utente; conservare sorgenti e storico; statistiche come proposta di revisione | Terza serie di risposte |
| Tentativi | Massimo due per skill e configurazione pertinente; una candidata alla volta | Terza serie di risposte |
| Aggiornamento non valido | Escludere la nuova versione; precedente disponibile soltanto se conservata, valida, abilitata e non ritirata | Terza serie di risposte, conferma della politica S03 |
| Sovrapposizione | Avviso, senza blocco automatico di ammissione | Terza serie di risposte |
| Prova di utilità | 100 skill e 20 richieste concordate; nessuna perdita sui richiami essenziali; obiettivo di riduzione del materiale di catalogo esposto almeno 30% rispetto a B; ricerca locale a regime p95 entro 300 ms, da verificare poi su 5.000 skill | Quarta serie di risposte, criteri sperimentali |

### 10.2 Stack e packaging scelti su delega

- **Runtime iniziale:** Python 3.13. Il runtime locale rilevato è 3.13.14; scegliere questa linea permette di iniziare sulla macchina disponibile senza introdurre un secondo servizio. Altre versioni e sistemi operativi richiedono proprie verifiche.
- **Archivio:** SQLite tramite il modulo standard `sqlite3`, FTS5 per la ricerca e tabelle relazionali per identità, dipendenze e statistiche. Il 17 ottobre 2026 una prova in memoria ha creato correttamente una tabella FTS5 con SQLite 3.50.4. Il prodotto dovrà verificare questa capacità all'avvio: la disponibilità locale non è una garanzia per ogni installazione Python.
- **CLI:** `argparse`, incluso nel runtime, per inventario, manutenzione, diagnostica e ricerca. Evita una dipendenza aggiuntiva per il primo insieme di comandi.
- **MCP:** SDK Python ufficiale, server locale con trasporto `stdio`; stessa logica applicativa esposta anche dalla CLI. Nessun server HTTP necessario per il percorso iniziale. I tool ricevono candidate generate dalla sessione, non avviano un modello in background.
- **Distribuzione:** pacchetto wheel, metadati in `pyproject.toml` e comando `fastskillretrieval` tramite entry point; installazione isolata con `pipx` da un artefatto locale verificato. La scelta non autorizza pubblicazione su PyPI o installazioni sul computer: in questo workspace non sono ancora stati creati pacchetti né installate dipendenze.
- **Verifica:** `pytest` per i comportamenti critici e prove attraverso i veri ingressi CLI/MCP; versione esatta delle dipendenze da bloccare al primo bootstrap, prima dei test d'integrazione.

**Alternative considerate:** TypeScript/Node resta tecnicamente possibile, ma per il nucleo locale Python offre già accesso a SQLite nella libreria standard e il runtime è disponibile sulla macchina di prova. Un eseguibile autonomo rimanderebbe la dipendenza da Python al processo di build, ma introduce lavoro di packaging specifico per piattaforma prima di aver verificato l'adattatore. Nessuna di queste motivazioni dimostra prestazioni su 5.000 skill: quello resta un obiettivo da collaudare.

**Superficie della prima prova:** Claude Code 2.1.302, Windows/PowerShell, runtime Python 3.13.14. È un bersaglio rilevato, non una matrice di compatibilità superata. Il rilevamento di Python ha emesso anche un avviso sulla risoluzione del proprio percorso, pur completando con esito zero la prova SQLite/FTS5: l'installazione isolata e il percorso dell'eseguibile vanno verificati nella prova di packaging.

Fonti ufficiali consultate per la scelta tecnica: [Python sqlite3](https://docs.python.org/3.13/library/sqlite3.html), [Python argparse](https://docs.python.org/3.13/library/argparse.html), [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/run/), [packaging di CLI Python](https://packaging.python.org/en/latest/guides/creating-command-line-tools/), [Claude Code e MCP](https://code.claude.com/docs/en/mcp). La documentazione conferma le interfacce generali, senza sostituire la prova sulle versioni locali o la verifica delle condizioni applicabili all'integrazione.

### 10.3 Qualità e ranking iniziali scelti su delega

La configurazione iniziale `rules-v1` usa controlli locali obbligatori e segnalazioni globali separate. I valori sotto sono decisioni tecniche iniziali, non soglie di efficacia dimostrate.

| Controllo locale | Criterio iniziale |
|---|---|
| Schema | Campi ammessi, tipi corretti, nessun campo generato per identità, permessi, fiducia o abilitazione; candidata JSON fino a 16 KiB |
| Completezza | Descrizione di 40–1.000 caratteri Unicode dopo normalizzazione NFC e rimozione degli spazi esterni; almeno una capacità con azione, oggetto e ambito e una condizione d'uso; esclusioni dichiarate come lista, anche vuota |
| Riscontro nella sorgente | Ogni capacità ha almeno un riferimento a file e passaggio effettivamente presenti nella versione sorgente; hash e riferimenti devono corrispondere. Un riferimento lessicale non certifica equivalenza semantica |
| Normalizzazione | Alias unici, non vuoti, al massimo 32 di 80 caratteri ciascuno; tecnologie canoniche tratte dal vocabolario versionato oppure esplicitamente non risolte, senza attribuire loro compatibilità automatica |

Ogni controllo vale 25 punti: il punteggio locale è la somma e l'ammissione richiede **100/100**, oltre a integrità delle risorse e politica d'uso. È un punteggio di conformità strutturale, non una probabilità di selezione corretta o una certificazione semantica. Una capacità dubbia, un'esclusione non chiarita o una tecnologia necessaria ma non risolta mantiene la candidata in `needs_revision`, anche se lo schema è formalmente corretto. I minimi sui campi evitano valori vuoti, ma non provano la qualità del linguaggio naturale.

La lunghezza consente descrizioni brevi ma verificabili; il limite superiore e i limiti agli alias contengono volume e ripetizioni. Sovrapposizione e distintività globale restano avvisi: non sottraggono punti locali e non provocano un inseguimento automatico delle descrizioni concorrenti.

Ranking iniziale `ranking-v1`: applicare prima ambito, abilitazioni, vincoli strutturati, compatibilità e disponibilità; usare poi FTS5/BM25 sui campi positivi, con pesi descrizione 5, capacità 4, condizioni d'uso 4, alias 3 e tecnologie 2. Le controindicazioni non sono indicizzate come segnali positivi. Ordinare per BM25 crescente e, a parità esatta, per ID stabile della skill crescente. Il punteggio locale e la frequenza di recupero non aggiungono un vantaggio nel ranking. Questi pesi rendono esplicita la preferenza iniziale per descrizione e capacità; il confronto S01 potrà motivare una loro revisione versionata.

Una richiesta esplicita di una skill prevale sul ranking ma non su permessi, disponibilità e budget. Il primo retriever non espande la sola vicinanza tematica: usa le relazioni per applicabilità, dipendenze e conflitti. Le dipendenze obbligatorie devono essere risolte prima della selezione finale, con limite iniziale di 32 identità distinte per chiusura; un superamento produce `dependency_unavailable` con motivo esplicito, senza dichiarare la dipendenza inesistente. Si tratta di un limite di lavoro, non di un massimo del catalogo.

### 10.4 Limiti operativi iniziali scelti su delega

Questi limiti proteggono la sessione da chiamate locali senza termine e da input eccessivi. Sono configurazione iniziale finita, da verificare sul campione; un loro aumento deve essere esplicito e registrato, senza disabilitare i controlli.

- Tempo massimo della ricerca locale: 2 secondi; lettura/caricamento e validazione di una candidata: 5 secondi per chiamata; inventario o pubblicazione: 30 secondi per chiamata. Sono limiti di esecuzione, distinti dagli obiettivi di latenza della prova di utilità.
- Inventario iniziale fino a 5.000 skill; al massimo 100 versioni nuove o modificate in un lotto di preparazione esplicitamente richiesto. I lotti successivi richiedono un nuovo comando o una richiesta esplicita che ne dichiari il numero, senza riavviare implicitamente il lavoro.
- Sorgente `SKILL.md` fino a 256 KiB; pacchetto fino a 32 MiB e 256 file ammessi; candidata fino a 16 KiB. Un superamento rimane visibile nell'inventario come limite operativo, senza troncare il contenuto e pubblicare una descrizione incompleta.
- Output dei tool fino a 64 KiB per risposta e comunque entro il budget di contesto residuo, applicando il limite più restrittivo. Diagnostica e inventari possono essere paginati; istruzioni operative non vengono troncate per simulare un caricamento riuscito. Una sorgente non completamente esaminabile nel budget resta da revisionare, senza inventarne i metadati.
- Una candidata alla volta, due tentativi per skill/versione/configurazione pertinente e una sola pubblicazione del catalogo alla volta. Il registro dei tentativi è locale; aprire una nuova sessione non azzera automaticamente il limite.
- Un timeout o un'interruzione conserva l'ultimo catalogo coerente, non riabilita versioni ritirate e non riparte automaticamente. Il prodotto può limitare i propri tool e rifiutare ulteriori tentativi; non può interrompere o imporre un tetto economico al ragionamento dell'intera sessione Claude Code.

Il limite dei log confermato riguarda il totale dei file di log attivi e ruotati; l'implementazione userà 100.000.000 byte come significato di 100 MB e 30 periodi di 24 ore come conservazione massima. Il database dei contatori aggregati e gli snapshot dei pacchetti sono dati applicativi separati, non un modo per conservare il testo delle ricerche vietato nei log.

### 10.5 Budget di contesto confermati

| Limite | Valore iniziale configurabile | Applicazione |
|---|---|---|
| Candidati principali | 5 al massimo | Non è una quota da riempire; le dipendenze sono esplicitate e ne viene incluso il costo |
| Schede candidate | 1.200 token complessivi | Comprende descrizioni, motivazioni, riferimenti e involucro serializzato emesso dal framework |
| Nuove istruzioni | 4.000 token per caricamento | Comprende il contenuto nuovo e le dipendenze necessarie del piano, senza frazionamenti artificiosi per aggirare il limite |
| Materiale gestito | 12.000 token per periodo di contesto | Include il materiale emesso dai tool del framework, anche durante la preparazione; non coincide con l'intero prompt del client |

I limiti si applicano insieme, usando quello più restrittivo. Quando un caricamento non entra, selezionare meno skill o produrre `budget_exceeded`; non troncare le istruzioni e non omettere silenziosamente una dipendenza obbligatoria. Il costo delle risorse lette successivamente entra nel conteggio al momento dell'emissione.

Contare il testo effettivamente serializzato con un tokenizer compatibile quando disponibile. Altrimenti usare una stima con margine dichiarato e registrare metodo, configurazione e natura stimata del valore, senza promettere un limite esatto sul prompt. Il sistema non deduce automaticamente lo spazio residuo da una finestra di contesto teorica.

Un nuovo periodo inizia quando il client fornisce un evento di compattazione o ripresa effettivamente osservabile. In mancanza di quel segnale, mantenere il conteggio conservativo oppure aprire una nuova sessione: non azzerarlo sulla sola supposizione che le istruzioni precedenti siano scomparse. Il periodo nuovo non dimostra la presenza o assenza di ogni istruzione nel contesto del modello.

### 10.6 Criteri sperimentali confermati

La prova iniziale usa 100 skill rappresentative e 20 richieste reali concordate con l'utente. Le 20 richieste concrete e i richiami essenziali attesi saranno fissati prima della prova, includendo i casi pertinenti al lavoro reale; non sono un corpus già raccolto né un benchmark statistico del comportamento LLM. Il confronto mantiene le condizioni A, B e C di S01, con identici metadati migliorati e snapshot comparabili fra B e C.

| Criterio | Obiettivo confermato | Evidenza richiesta |
|---|---|---|
| Richiami essenziali | Nessuna perdita nei casi concordati | Elenco atteso fissato prima della prova; skill necessarie ancora richiamabili, con dipendenze e risorse disponibili. Distinguere recupero e caricamento dall'applicazione effettiva da parte del modello |
| Materiale di catalogo esposto | Almeno 30% in meno in C rispetto a B | Token osservabili delle descrizioni e degli altri elementi del catalogo effettivamente esposti, includendo punto d'ingresso, schede e catalogo nativo eventualmente ancora presente. Non misurare soltanto l'output del retriever |
| Latenza della ricerca | p95 non superiore a 300 ms a regime | Misure della ricerca locale con runtime e cache del database già avviati; avvio a freddo, generazione e latenza del modello riportati separatamente |
| Scala | Prima 100 skill, poi fino a 5.000 | Ripetere la verifica di latenza sul catalogo esteso prima di dichiarare raggiunto l'obiettivo di scala |

Per il confronto di volume usare lo stesso punto di osservazione nei task B e C, fissato prima delle esecuzioni, e la stessa unità di conteggio. Calcolare la riduzione come `1 - M_C / M_B`, dove M è il materiale medio di catalogo esposto sui 20 casi e `M_B > 0`. Riportare anche i singoli casi, le nuove istruzioni caricate e i costi di preparazione/manutenzione: una riduzione delle descrizioni non dimostra da sola una riduzione dell'intero prompt o del costo complessivo.

Se il client non rende misurabile il materiale di catalogo esposto o se la condizione B non può essere riprodotta fedelmente, il relativo beneficio resta **non dimostrato**. Una stima va etichettata come tale e non sostituisce un risultato osservato per dichiarare superato questo criterio.

Protocollo iniziale della misura di latenza, scelto su delega: dieci ricerche di riscaldamento non conteggiate, poi cinque esecuzioni per ciascuna delle 20 query, in ordine registrato e mescolato, per 100 campioni. Il p95 è il 95º valore dei 100 tempi ordinati. Misurare l'intero percorso locale di ricerca, dalla query strutturata alla risposta serializzata, inclusi filtri, dipendenze e selezione; distinguere le risposte interamente servite da una cache di risultati. Registrare hardware, versioni e configurazione. Questi campioni descrivono la prova eseguita, senza attribuire loro garanzie statistiche generali. Le misure sperimentali sono separate dai contatori di domanda reale.

La decisione di proseguire con il retriever richiede il confronto dei risultati con questi criteri. Un fallimento è un esito della prova da analizzare, non una ragione per modificare retroattivamente la soglia e dichiarare successo; resta possibile proseguire con il solo preparatore secondo S01. Nessun criterio di questa sezione risulta ancora verificato.

### 10.7 Chiusura delle dodici domande iniziali

| Domanda del brainstorming §16 | Scelta acquisita per la prima versione |
|---|---|
| 1. CLI o launcher | Claude Code CLI direttamente, senza launcher |
| 2. Ambito del budget | Solo materiale del framework, con valori configurabili del §10.5 |
| 3. Dimensione del catalogo | Obiettivo fino a 5.000; prova iniziale su 100 |
| 4. Provenienza | Catalogo personale misto |
| 5. Originali o metadati separati | Metadati separati |
| 6. Soglie e tentativi | Regole iniziali del §10.3; due tentativi, una candidata alla volta |
| 7. Frequenza di manutenzione | Nessuna frequenza automatica; solo su comando |
| 8. Versione modificata non valida | Nuova versione esclusa; precedente solo se conservata, valida, abilitata e non ritirata |
| 9. Archiviazione | Su scelta dell'utente, reversibile, conservando sorgenti e storico |
| 10. Dati delle ricerche | Log locali minimi, massimo 30 giorni e 100 MB; niente testo delle richieste o contenuto dei file |
| 11. Copertura obbligatoria | Nessuna operazione nella prima versione: modalità suggerimento |
| 12. Client e versioni | Prima prova su Claude Code CLI 2.1.302, Windows/PowerShell; Codex rinviato |

**Stato:** chiuse come scelte di progetto per il perimetro iniziale. Restano da implementare e provare i comportamenti descritti, verificare il percorso d'integrazione e le condizioni applicabili, scegliere i 20 casi concreti prima dell'esperimento e misurare il beneficio. I dodici rilievi della revisione ECC non vengono marcati come tecnicamente risolti per il solo fatto di aver risposto alle domande.
