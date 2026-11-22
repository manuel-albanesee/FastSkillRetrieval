# FastSkillRetrieval — Revisione critica con ECC

Data: 17 ottobre 2026.

Documento analizzato: `FastSkillRetrieval_Brainstorming.md`, 404 righe. Nel workspace è presente il brainstorming; non è presente un'implementazione da collaudare. Questa revisione individua rischi progettuali e propone verifiche: non attesta difetti di codice, risultati sperimentali o decisioni già approvate.

Metodo: ECC Product Lens per premessa e valore; Council con tre letture indipendenti (Skeptic, Pragmatist, Critic); Eval Harness per criteri osservabili; Agent Security Hardening per i confini di fiducia. Consultate anche le fonti ufficiali elencate vicino alle osservazioni pertinenti.

## Valutazione principale

Il rischio prioritario è costruire un buon motore di ricerca che non migliori il lavoro nel client effettivo. Prima del nucleo completo proposto nel §17, suggerisco una prova verticale su un solo client/versione: preparare un piccolo catalogo reale, recuperare una skill e renderla utilizzabile preservandone il comportamento, misurando il materiale aggiunto e l'overhead.

La conclusione resta coerente con la mia posizione iniziale; il Council ha aggiunto due rischi che cambiano il contenuto della prova: la copertura persa durante l'ammissione al catalogo e la coerenza delle versioni tra ricerca e caricamento.

Distinzioni usate sotto:

- **Gap:** comportamento o criterio non definito nel testo.
- **Da rendere operativo:** rischio già riconosciuto, ma privo di una decisione o prova risolutiva.
- **Scelta deliberata:** indirizzo esplicito da rispettare. Non viene trasformato in requisito contrario.

## 1. Il confronto deve isolare il valore del retriever — alta, gap

**Riferimenti:** §§1, 4, 17; righe 9–15, 51–58, 389–391.

Il prodotto combina almeno due possibili miglioramenti: descrizioni migliori e un meccanismo di selezione nuovo. Un confronto tra situazione attuale e framework completo non permetterebbe di attribuire il beneficio. Potrebbe bastare correggere le descrizioni o ridurre le sovrapposizioni, senza introdurre un servizio durante il lavoro.

**Aggiunta proposta:** definire un utilizzatore iniziale, episodi reali di mancato recupero e tre condizioni di confronto: catalogo attuale; catalogo con metadati migliorati; stessi metadati con retriever. Registrare separatamente costo iniziale di preparazione, costo della manutenzione e beneficio durante il task.

**Verifica minima:** richieste reali, giudizio umano sulla pertinenza e misura dell'overhead. Questa è una verifica della premessa; non impone il benchmark comportamentale LLM rinviato dal §15.

## 2. Ridurre il catalogo nativo è una condizione di fattibilità — alta, da rendere operativo

**Riferimenti:** §§10, 13; righe 230–238, 337–341.

Il documento riconosce già il doppio catalogo. Manca una prova che il framework possa controllare l'esposizione delle skill sulla superficie scelta, mantenendo invocazione esplicita, risorse e proprietà necessarie. Nascondere le descrizioni potrebbe anche rimuovere il segnale che induce l'agente a chiamare il retriever: occorre un punto d'ingresso sempre disponibile, comprensibile e poco costoso.

Claude documenta già caricamento progressivo, controllo dell'invocazione e strumenti per intervenire sulle descrizioni nel catalogo: costituiscono una baseline da confrontare, non una prova che il retriever sia superfluo. [Claude Code — Skills](https://code.claude.com/docs/en/skills).

**Aggiunta proposta:** una matrice di capacità per client/versione con scoperta, visibilità, attivazione, risorse, eventi e misurabilità. Il risultato della prova deve esplicitare quali proprietà vengono preservate e quali no.

**Verifica minima:** sessione nativa e sessione integrata sullo stesso task; osservare catalogo esposto, contenuto emesso e skill effettivamente caricata. Una riduzione dei soli token emessi dal retriever non dimostra una riduzione del prompt complessivo.

## 3. I contatori non vedono le skill escluse dall'ingest — alta, gap

**Riferimenti:** §§4.1, 12; righe 53–62, 306–333.

La regola di non pubblicare descrizioni insufficienti è esplicita e viene rispettata. La conseguenza non è però coperta dalle metriche: una skill esclusa non entrerà mai nel TOP N. I suoi zero recuperi non distinguono scarsa domanda, cattiva descrizione e assenza dal catalogo attivo. La revisione statistica potrebbe trascurare proprio le skill che più necessitano di correzione.

**Aggiunta proposta:** mostrare inventario scoperto, ammesse, in lavorazione ed escluse, con ragione e anzianità; distinguere assenza dall'indice da mancato recupero. Conservare accesso alla diagnostica delle escluse senza inserirle nel retrieval attivo.

**Verifica minima:** una skill utile con descrizione insufficiente deve risultare chiaramente esclusa e revisionabile; non deve apparire come una normale skill attiva mai richiesta.

## 4. Qualità dei metadati, fiducia e autorizzazione sono dimensioni separate — alta, gap

**Riferimenti:** §§4.2, 4.3, 5; righe 66–76, 97–109.

Il manutentore legge istruzioni di terzi: una skill può contenere richieste di alterare il ranking, modificare altre skill o ignorare il contratto di riscrittura. Il semplice riscontro lessicale può anche trovare una capacità citata soltanto per negarla. Passare i controlli descrittivi non certifica sicurezza o veridicità.

**Aggiunta proposta:** modellare separatamente qualità, origine/fiducia e possibilità d'uso. Il processo di preparazione tratta il corpo come dato, restituisce soltanto campi ammessi e non esegue gli script della skill. Il retrieval non conferisce permessi aggiuntivi. Lo stesso vale per richieste, manifest ed errori usati come segnali: un testo che cita una tecnologia non autorizza ad agire su quella tecnologia.

**Verifica minima:** fixture con istruzioni ostili, capacità negate e keyword artificiosamente ripetute; controllare che non producano esecuzioni, modifiche estranee o autorizzazioni implicite. Non è una certificazione semantica completa.

## 5. L'unità versionata deve comprendere anche le risorse — alta, gap parziale

**Riferimenti:** §§4.3, 9.2, 11, 13; righe 76, 211–216, 279–290, 341.

Hash e pubblicazione atomica sono previsti. Manca il contratto tra risultato di ricerca e contenuto successivamente caricato: nel frattempo il fornitore può aggiornare la directory. Cambiare una risorsa relativa senza cambiare `SKILL.md` può modificare il funzionamento pur lasciando identici i metadati indicizzati.

**Aggiunta proposta:** definire l'unità di versione del pacchetto, le risorse incluse e la verifica al caricamento; usare snapshot coerenti oppure rilevare il cambiamento e richiedere un nuovo recupero. Scegliere anche la politica per le sessioni già aperte.

**Verifica minima:** aggiornamento, eliminazione e modifica di una risorsa tra ricerca e lettura; il sistema deve restituire contenuto coerente o un esito esplicito di versione cambiata.

## 6. Aggiornare una skill non revoca le istruzioni già in conversazione — alta, gap parziale

**Riferimenti:** §§9.2, 10.2; righe 211–216, 230–234.

Il limite di non poter rimuovere il contesto è già riconosciuto. La deduplicazione per ID e versione permette però di caricare sia v1 sia v2: se si contraddicono, il vecchio contenuto rimane. Ripristinare uno stato dopo compattazione può inoltre scambiare una registrazione di precedente caricamento per presenza attuale delle istruzioni.

**Aggiunta proposta:** distinguere contenuto emesso, caricamento osservato e presenza corrente non verificabile. Definire quando mantenere la versione della sessione, quando introdurre una sostituzione esplicita e quando richiedere una sessione nuova. Il ripristino conserva anche vincoli e correzioni dell'utente, senza reiniettare obiettivi superati.

**Verifica minima:** due versioni incompatibili durante una sessione e un recupero dopo compattazione; esito documentato senza promettere cancellazione del contesto.

## 7. Nessuna corrispondenza, guasto e blocco devono essere distinguibili — alta, gap

**Riferimenti:** §§7.1, 9.1; righe 142, 202–207.

Il no-match è previsto, ma non basta come contratto di risposta. Catalogo non pronto, database indisponibile, dipendenza mancante, versione cambiata e budget insufficiente richiedono azioni diverse. Una copertura obbligatoria può diventare irraggiungibile se la skill richiesta resta esclusa dall'ingest.

In Codex gli hook non gestiti devono essere esaminati e considerati attendibili; gli aggiornamenti possono richiedere una nuova verifica. Inoltre alcuni errori o timeout di callback `PreToolUse` possono non bloccare lo strumento. La modalità obbligatoria richiede pertanto una prova del percorso specifico, non il solo hook configurato. [OpenAI — Hooks](https://learn.chatgpt.com/docs/hooks).

**Aggiunta proposta:** esiti tipizzati e comportamento dichiarato per suggerimento e copertura obbligatoria. Nella seconda, una condizione non verificabile deve essere visibile; eventuali eccezioni dipendono da una politica esplicita, non da un successo simulato.

**Verifica minima:** primo avvio con indice incompleto, servizio spento, timeout, dipendenza esclusa e budget troppo piccolo. Nessun caso deve essere registrato come ricerca riuscita senza corrispondenze.

## 8. Identità e ambito non coincidono con il nome — media, gap parziale

**Riferimenti:** §§9.2, 14; righe 212–216, 349–356.

L'identità stabile è prevista, ma non sono definite le collisioni fra skill globali, di progetto, plugin, fork e copie identiche. Deduplicare per nome può unire istruzioni diverse; deduplicare solo per hash può eliminare differenze di ambito, fiducia o configurazione.

La documentazione Codex precisa che skill omonime non vengono fuse e possono apparire entrambe nei selettori. [OpenAI — Build skills](https://learn.chatgpt.com/docs/build-skills).

**Aggiunta proposta:** identità con origine, namespace e ambito; distinguere copia identica da alternativa funzionale; definire precedenze, abilitazione locale e dipendenze che richiedono una fonte specifica. Evitare che un catalogo personale esponga metadati riservati di un altro progetto.

**Verifica minima:** due skill omonime con istruzioni opposte in ambiti diversi; la selezione deve spiegare origine e ragione della scelta.

## 9. Deterministico non significa indipendente dal catalogo — media, gap

**Riferimenti:** §§5.2, 7, 8, 11; righe 103–109, 133–138, 162, 279–290.

La specificità rispetto alle concorrenti dipende dall'intero catalogo. Aggiungere una skill può quindi invalidare il punteggio di altre skill anche se i loro file non cambiano. Anche il ranking testuale può cambiare quando cambia il corpus. Aggiornare solo gli elementi con hash diverso non è necessariamente sufficiente.

**Aggiunta proposta:** versionare anche vocabolario, normalizzazione, regole, parametri e insieme dei documenti; definire quali controlli sono locali e quali richiedono rivalutazione globale. Ordinamento stabile a parità di punteggio. Il contratto riproducibile deve essere: stessa query strutturata, stesso snapshot e stessa configurazione producono lo stesso risultato.

**Verifica minima:** inserire una skill molto simile a una esistente e controllare le invalidazioni previste. Evitare riscritture a catena che cambiano continuamente il criterio con cui si valutano a vicenda.

## 10. La normalizzazione tecnica merita casi dedicati — media, gap parziale

**Riferimenti:** §§5.2, 7.2, 8; righe 100, 156–162.

Sinonimi, negazioni e vocabolario sono già considerati. Rimangono da specificare linguaggi con punteggiatura (`C`, `C++`, `C#`), nomi di pacchetto, versioni e frasi multilingue. Una query FTS non deve essere costruita trattando direttamente il messaggio dell'utente come espressione della sintassi di ricerca.

FTS5 consente di configurare i caratteri dei token; lo stemmer Porter è progettato per l'inglese. Da ciò segue la necessità di verificare la rappresentazione dei termini tecnici e dell'italiano, non l'inadeguatezza di SQLite. [SQLite — FTS5](https://www.sqlite.org/fts5.html).

**Aggiunta proposta:** campi strutturati per vincoli e termini esatti, mapping espliciti e costruzione sicura dell'espressione di ricerca. Stabilire come separare indicazione positiva e controindicazione: il termine citato in «non usare per X» non dovrebbe diventare un semplice incentivo a recuperarla per X.

**Verifica minima:** coppie tecniche confondibili, esclusioni e richieste in italiano con nomi di API inglesi.

## 11. Manca un budget operativo oltre a quello in token — media, gap parziale

**Riferimenti:** §§10, 11, 16; righe 218–290, 379–380.

Tentativi e frequenza sono già domande aperte. Mancano obiettivi di latenza e limiti al costo complessivo: preparare tutte le skill, correggerle e rivalutare relazioni può diventare più oneroso del beneficio. Il modello di riscrittura può cambiare output e costo anche con identici input; va distinta la riproducibilità del retrieval da quella della preparazione.

**Aggiunta proposta:** limiti configurabili per tempo, spesa, concorrenza e volume; esito a budget esaurito; cache legata a sorgente, regole e versione del processo di generazione; diagnosi comprensibile delle skill rimaste in lavorazione. Trattare anche spazio disco insufficiente, lock e interruzione durante la pubblicazione.

**Verifica minima:** una skill che non supera mai la validazione e una manutenzione interrotta non devono consumare risorse senza limite o rendere inutilizzabile un catalogo coerente già disponibile.

## 12. I contatori hanno bisogno di un denominatore operativo — media, gap

**Riferimenti:** §12; righe 312–333.

Il testo distingue già esposizione e successo ed esclude la popolarità dal ranking: non è una lacuna. Rimane però difficile confrontare due periodi se cambiano il numero di query, la disponibilità della skill, il tipo di lavoro o la configurazione TOP N. Una crescita dei recuperi può derivare soltanto da più traffico.

**Aggiunta proposta:** conservare numero delle ricerche riuscite e fallite, periodo di effettiva ammissione, filtri e versione del retriever. Mostrare frequenze normalizzate con queste condizioni visibili. La domanda potenziale di una skill non è deducibile dai soli contatori: se manca una classificazione delle richieste, dichiarare il limite senza inventare un tasso di utilità.

**Verifica minima:** raddoppiare le query mantenendo invariata la distribuzione non deve essere presentato come miglioramento della descrizione.

## Council: decisione e dissenso

- **Architect, posizione iniziale:** verificare prima il controllo sul catalogo nativo. Le ragioni principali sono il risparmio di contesto ancora da dimostrare, la differenza tra lettura e invocazione e il costo di mantenere due adattatori. Rischio: una prova troppo piccola può sottostimare il beneficio a grande scala.
- **Skeptic:** confrontare anche la semplice pulizia del catalogo; il framework deve dimostrare valore aggiuntivo. Sottolinea la perdita di copertura delle skill escluse dal gate.
- **Pragmatist:** partire da configurazione e primo recupero utile su una superficie reale, con costi e overhead osservabili.
- **Critic:** procedere con un MVP ristretto soltanto dopo verifiche dei confini di fiducia, delle versioni e degli esiti negativi.

**Accordo:** niente nucleo completo prima della prova verticale. **Dissenso più forte:** lo Skeptic contempla che basti migliorare il catalogo e che il framework non serva; il Critic considera plausibile un MVP circoscritto. La proposta raccomandata mantiene aperta entrambe le conclusioni: se i metadati migliorati bastano, il primo prodotto utile può essere il preparatore/manutentore.

## Esperimento proposto prima del §17

Proposta da discutere, senza modificare le decisioni del brainstorming:

1. Scegliere un utilizzatore, un client/versione e un catalogo reale. Concordare quale beneficio e quale overhead rendono utile il prodotto.
2. Inventariare ammesse ed escluse e preparare i metadati rispettando il gate pre-ingest. Usare un piccolo insieme di richieste rappresentative, incluse attività rare e nessuna corrispondenza; la numerosità va concordata, non trattata come soglia scientifica.
3. Confrontare catalogo attuale, soli metadati migliorati e retriever con gli stessi metadati. Distinguere metriche osservate da stime del prompt.
4. Provare il percorso ricerca → caricamento di una skill con una risorsa relativa, un aggiornamento concorrente e una dipendenza non disponibile.
5. Provare indice assente, timeout e aggiornamento non valido. Verificare le differenze fra modalità di suggerimento e copertura obbligatoria.
6. Decidere se proseguire con il retriever, limitarsi al preparatore oppure cambiare integrazione. Nessuna conclusione sulla qualità generale del modello deriva da questa prova ridotta.

Per questa fase rinvierei il secondo adattatore, lo scheduler persistente e l'espansione relazionale elaborata. Non propongo di eliminare la manutenzione o il grafo dall'obiettivo del prodotto.

## Cosa non va contato come dimenticanza

Il brainstorming tratta già negazioni, dipendenze cicliche, pubblicazione atomica, rollback, deduplicazione, contesto non revocabile, privacy dello storico come scelta aperta, interpretazione prudente dei contatori e limiti degli hook. Lingua/stack, frequenze, soglie, provenienza del catalogo e versioni supportate sono decisioni aperte dichiarate. I benchmark comportamentali LLM e la ricerca semantica sono rinvii deliberati.

Le nuove proposte richiedono criteri di accettazione e piccoli casi osservabili; non trasformano automaticamente questi rinvii in requisiti della prima versione.
