# Prima implementazione — registro di esecuzione

Data: 17 ottobre 2026. Ambiente: Windows/PowerShell, Python 3.13.14, SQLite 3.50.4 con FTS5 disponibile, Claude Code 2.1.302.

Repository Git inizializzato con branch `main`, senza remoti. Nessun commit o push eseguito.

## Perimetro

Prima prova verticale in modalità assistenza: inventario esplicito di sorgenti autorizzate, metadati separati, validazione, pubblicazione, ricerca e caricamento con risorse relative. Nessuna modifica del catalogo nativo o configurazione globale. Catalogo gestito, benchmark di utilità su 100 skill e 20 richieste reali, e scala di 5.000 skill restano da verificare.

## Avvio richiesto con Claude Code

È stato effettivamente avviato il CLI autenticato con strumenti limitati a Read/Write/Edit/Glob/Grep, senza persistenza della conversazione e senza server MCP esterni. Il servizio ha risposto HTTP 429, `usage_limit_reached`, prima di qualsiasi generazione o modifica. Ripristino indicato dal client: 17 ottobre 2026 alle 19:10 Europe/Rome. Nessun costo API riportato dalla chiamata. Questo tentativo non costituisce implementazione o verifica riuscita con Claude Code.

## Implementazione autorizzata con Codex

Dopo il blocco di quota, l'utente ha autorizzato implementazione e test locali con Codex e richiesto un promemoria per ripetere le prove con Claude Code. È stato creato il promemoria nella chat, con indicazione di non notificare prima delle 19:10 e prima del completamento del lavoro.

Realizzato il percorso inventario esplicito → ispezione → candidata strutturata → validazione → pubblicazione atomica → ricerca FTS5/BM25 → caricamento → lettura di risorse relative, attraverso CLI e server MCP stdio. Tutta la logica è condivisa; l'adattatore usa MCP Python SDK 1.29.0. Le dipendenze dirette e transitive sono bloccate in `uv.lock`, installate nel solo `.venv` del progetto.

Gli originali non vengono modificati. L'identità include la radice autorizzata; uno stato non può essere riusato per una radice diversa. Si rifiutano traversal, symlink/reparse point e campi generati fuori schema. Il limite di due tentativi sopravvive a nuove sessioni e riavvii. Il manifesto comprende le risorse; prima della pubblicazione si verifica lo snapshot completo e prima dell'uso la sua integrità. Vecchi snapshot validi possono sopravvivere a nuovi aggiornamenti non validati, fino al ritiro esplicito.

Il budget usa i byte UTF-8 della risposta JSON come stima conservativa, comprende l'involucro applicativo e le letture di preparazione. Il piano di caricamento include preventivamente tutte le risorse testuali e le emissioni ripetute condividono il limite cumulativo della skill nella sessione. Le risposte di rifiuto hanno overhead inevitabile, conteggiato anche dopo il limite: non viene emesso nuovo materiale. Non è una misura esatta del tokenizer, dell'involucro aggiuntivo del client MCP o dell'intero prompt Claude.

## Risultati delle verifiche locali

Verifica adattata da ECC Verification Loop al progetto Python, senza strumenti di controllo tipi non configurati.

| Verifica | Risultato osservato |
|---|---|
| Suite completa nell'ambiente `.venv` | **39 passed, 1 skipped**, 25,48 secondi nell'esecuzione con copertura dei sottoprocessi |
| Copertura delle istruzioni Python | **91%** complessivo, 626 istruzioni; core catalogo 89%, contratti 93%, pacchetti 88%, CLI 95%, server MCP 100% |
| CLI reale | Inventario, submit, publish, search, load, resource, esiti JSON e codici di uscita collaudati attraverso processi Python |
| MCP reale | Processo server avviato dal client SDK ufficiale; handshake, elenco di otto tool, percorso completo e rifiuti effettivamente verificati |
| `ruff check src tests scripts` | Superato |
| `compileall -q src` | Superato: verifica sintattica, non controllo tipi statico |
| Build wheel e archivio sorgente | Generazione locale riuscita; nessuna pubblicazione esterna |
| Installazione wheel | Ambiente separato `.work/wheel-venv`, dipendenze runtime esportate da `uv.lock` e installate offline; import confermato da `site-packages`, diagnostica CLI e prova MCP con `--installed` superate |
| Launcher console Windows | Il file `.exe` generato è presente, ma il sistema restituisce `Accesso negato` all'avvio; funziona il percorso `python -m fastskillretrieval`, usato dalla configurazione Claude |
| Configurazione successiva prova Claude | `scripts/prepare_claude_test.py` eseguito; produce `.work/claude-test/mcp-config.json` e `prompt.txt` senza avviare Claude |
| Git | Branch `main`, nessun remoto, nessun commit o push |

Il test saltato tenta di creare un symlink reale: questo utente Windows non possiede il permesso necessario. Un test separato del controllo dell'attributo Windows reparse point passa, ma non sostituisce una prova reale di tutti i tipi di collegamento.

La prova MCP finale della wheel completa tredici chiamate, comprese una candidata con campo vietato e l'esaurimento dei due tentativi. Il manifesto della wheel e dell'archivio sorgente è stato controllato: nessun `.work`, `.venv` o `.git`; l'archivio sorgente include fixture, helper e `conftest.py` per riprodurre i test. Il launcher console non viene dichiarato collaudato con successo e non sono stati modificati criteri di sicurezza Windows per abilitarlo.

I test includono candidate ostili/fuori schema, evidenze inventate, due tentativi persistenti, nessuna corrispondenza distinta da catalogo non pronto, ricerca FTS con sintassi ostile, esclusioni non positive, tecnologie distinte, aggiornamenti delle risorse, manomissione dello snapshot, fallimento simulato di pubblicazione, lock SQLite, timeout, versioni di sessione, ritiro, budget preventivo e cumulativo. La verifica di sicurezza del codice non trova invocazioni shell, `eval`/`exec`, provider LLM o credenziali nel runtime; non costituisce certificazione della sicurezza delle skill o del modello host.

### Riproduzione

```powershell
uv sync --frozen --extra dev
$python = (Resolve-Path .venv/Scripts/python.exe).Path
& $python -m coverage erase
$env:FSR_COVERAGE = '1'
& $python -m coverage run --parallel-mode -m pytest -q --basetemp=.work/pytest-coverage
& $python -m coverage combine
& $python -m coverage report
Remove-Item Env:FSR_COVERAGE
& $python -m ruff check src tests scripts
& $python scripts/verify_mcp.py
uv build --out-dir dist
```

Il sandbox di Codex ha impedito l'accesso ad alcune directory temporanee, la creazione del processo MCP e alcune rinomine della build. I test sono stati spostati in `.work`; installazione locale, build e suite con subprocess sono poi riusciti con esecuzione autorizzata fuori dal sandbox. Non si tratta di un rifiuto di approvazione automatica. Il Python di base continua a emettere l'avviso sul proprio percorso già rilevato in progettazione. L'SDK emette un avviso Pydantic sulla forward reference `lifespan`; il percorso MCP collaudato funziona con le versioni bloccate.

## Cosa resta da verificare o implementare

- **Claude Code:** non ancora collaudato nel modello/client effettivo; disponibile configurazione e prompt per la prova successiva. L'esito SDK non dimostra applicazione delle istruzioni da parte di Claude.
- **Catalogo gestito:** non supportato. Nessuna modifica alla visibilità delle skill native; nessuna promessa di risparmio globale di contesto.
- **Semantica nativa:** frontmatter esecutivo e sostituzioni dinamiche vengono bloccati, non emulati. La lettura del corpo non preserva invocazione nativa, permessi, fork o hook. Il controllo iniziale è prudenziale, non un parser completo di tutte le estensioni.
- **Preparazione:** conformità strutturale ed evidenza lessicale, senza certificazione semantica. Revisione umana delle candidate; nessuna riscrittura automatica o inferenza autonoma.
- **Funzioni successive:** grafo e dipendenze obbligatorie, conflitti, rollback comandato e idempotenza per request ID. I contatori attuali misurano le chiamate, non domanda deduplicata né probabilità di utilità. Nessun log persistente di richieste o contenuti.
- **Scala e utilità:** nessuna prova sulle 100 skill/20 richieste reali concordate, sulla riduzione di catalogo del 30%, sul p95 del catalogo reale o su 5.000 skill. La scansione oltre cento versioni nuove/modificate si interrompe senza modificare il catalogo attivo; la suddivisione di grandi cataloghi in lotti rimane da estendere.
- **Tempi:** scadenze cooperative nelle operazioni e nel motore SQLite, non interruzione forzata di qualsiasi chiamata di sistema bloccata.

Questi limiti non sono marcati come risolti per il solo fatto che la prova verticale locale passa.
