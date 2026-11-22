# Verifica host Capability Retrieval

Data: 18 ottobre 2026, Europe/Rome. Prove eseguite direttamente con Claude Code CLI;
le ricevute runtime sono separate dai dati del registry e dalle dichiarazioni del modello.

## Ambiente e isolamento

| Componente | Versione/superficie osservata |
|---|---|
| Sistema | Windows 11, build 26200, PowerShell |
| Python | 3.13.14, ambiente `.venv` del repository |
| SDK MCP | 1.29.0, trasporto `stdio` |
| pytest | 8.3.5 |
| Claude Code | 2.1.302, CLI `--print`, autenticazione attiva |
| Modello dichiarato dal client | `claude-opus-5-5` |

Nessuna installazione di skill/agent, manutenzione del catalogo personale o modifica delle
impostazioni globali. La prova MCP usa una copia della fixture preesistente sotto
`.work/host-proof/claude-state`; i sorgenti di esempio sono letti soltanto. Configurazione
MCP esplicita con `--strict-mcp-config`, impostazioni personali/progetto escluse,
`--restricted`, built-in tool disabilitati, quattro tool MCP di lettura consentiti,
`dontAsk` e prompt di autorizzazione disabilitati. Nessun bypass dei permessi.
Il client elenca gli otto tool offerti dal server; le chiamate osservate usano solo i quattro
consentiti. Le sessioni sono temporanee con `--no-session-persistence`.

Un primo tentativo nella sandbox è scaduto dopo 120 secondi; non stabilisce né indisponibilità
del servizio né quota esaurita. Un solo tentativo con accesso di rete autorizzato è poi
riuscito. Ogni client ha timeout di 120 secondi. I dati grezzi restano in `.work/host-proof/`
e non sono copiati in questo documento.

## Matrice delle prove

| Capacità | Server SDK locale | Claude Code effettivo | Delega nativa effettiva |
|---|---|---|---|
| Negoziazione e trasporto MCP | Coperti dallo smoke di baseline; tentativo supplementare sandbox bloccato e terminato | **verified**: server `connected`, processo exit 0 | Non coinvolge MCP |
| Diagnostica e discovery legacy | Contratto della baseline | **verified**: `assistance`, un risultato | Non applicabile |
| Emissione corpo skill | Contratto della baseline | **verified**: `content_emitted_not_application_confirmed` | Non prova invocazione nativa della skill |
| Risorsa e confine pacchetto | Contratto della baseline | **verified**: marker presente, traversal `policy_denied` | Non applicabile |
| Ricerca senza risultato | Contratto della baseline | **verified**: `no_match`, lista vuota | Non applicabile |
| Subagent temporaneo | Non simulato nel server | Strumento nativo disponibile | **verified**, singola chiamata `Agent`, hand-back con marker |
| Nessun tool eseguito dal subagent | Non applicabile | Ricevuta runtime | **verified**: `tool_uses: 0` |
| Assenza di strumenti accessibili al subagent | Non applicabile | Configurato `tools: []`, superficie effettiva non esposta dalla ricevuta | **unverified**: zero chiamate non prova enforcement |
| Preload di skill nel subagent | Non provato | **unverified** | **unverified** |
| Gestione catalogo nativo, hook e policy | Non implementati da questa prova | **unverified** | **unverified** |
| Integrazione dispatch registry → runtime | Nessun subprocess LLM introdotto nel framework | Prova CLI separata | **unverified**: non costituisce esecuzione end-to-end del planner |
| Budget del contesto/deleghe ed equivalenza fra client | Stime del framework distinte | **unverified** | Uso riportato dal runtime, nessun controllo budget dimostrato |

La baseline SDK è quella della suite locale coordinata dall'implementazione; la prova
Claude qui registrata è autonoma. Il tentativo SDK supplementare nella sandbox non ha
prodotto un risultato ed è stato terminato esplicitamente: non viene contato come successo.

## Ricevute Claude MCP

Processo concluso con exit 0, risultato runtime `success`, nessun errore, sette turni,
circa 17 secondi. Le sei chiamate reali, estratte dai blocchi `tool_use`/`tool_result`, sono:

| Tool | Riscontro effettivo |
|---|---|
| `catalog_diagnostics` | `status=ok`, `mode=assistance` |
| `search_skills` | `status=ok`, una candidata |
| `search_skills` | `status=no_match`, zero candidate |
| `load_skill` | `status=ok`, `content_emitted_not_application_confirmed` |
| `read_skill_resource` | `status=ok`, marker `FSR-MARKDOWN-CHECK-01` nel contenuto |
| `read_skill_resource` | `status=policy_denied`, `reason=resource_outside_package` |

L'ordine della ricerca senza risultato è indipendente dal caricamento. Nessuna conclusione
sulla qualità semantica, applicazione delle istruzioni o riduzione di contesto è dedotta
dall'emissione del corpo.

Evidenze locali: `.work/host-proof/command.json`, `claude-mcp.stdout.jsonl`,
`claude-mcp.stderr.txt`, `observed-checks.json`, `result.json`.

## Prova nativa minima

Definizione temporanea passata con `--agents` da file, descrizione innocua e prompt che
richiede soltanto il marker `FSR-NATIVE-AGENT-PROBE-01`, `tools: []`. Il main espone solo
lo strumento di delega; nessun MCP viene collegato. Il framework non avvia il client:
questa è una prova esplicita dell'operatore nell'ambito del collaudo.

Il runtime ha emesso una chiamata `Agent` con `subagent_type=fsr_probe`, seguita da
`tool_result` con hand-back del marker, identificatore del subagent, `tool_uses=0`,
`subagent_tokens=1267` e durata di circa 1,9 secondi. La sessione main si è conclusa
con exit 0 in circa 7 secondi. L'inizializzazione elenca il tool come `Task`, mentre la
chiamata osservata si chiama `Agent`: non assumere nomi equivalenti in altri client/versioni.

Il messaggio finale del modello afferma che l'ambiente presentava al subagent “All tools”.
È un self-report, non una ricevuta di enforcement; perciò la prova dimostra una delega
senza utilizzo di strumenti, senza certificare che `tools: []` impedisca ogni strumento
in questa versione. Non si espande questa prova a deleghe arbitrarie.

Le metriche runtime disponibili sono annotate separatamente dalle stime FSR. Non provano
la dimensione completa del contesto del subagent, una fatturazione effettiva o l'applicazione
dei limiti di budget del framework. Cancellazione, ritiro fra discovery e attivazione,
preload e fallimenti della delega non sono stati provati nel client.

Evidenze locali: `.work/host-proof/agents.json`, `agent-command.json`,
`native-agent.stdout.jsonl`, `native-agent.stderr.txt`, `agent-result.json`.

## Diagnosi baseline e limiti

La prima suite coordinata ha riportato un errore in
`test_cli_actual_commands_and_exit_codes`: `publish` terminava con exit 2.
La fixture fallita è stata copiata in `.work/host-proof/baseline-state`; il comando reale
CLI `publish` sulla copia e sulla stessa radice fixture è riuscito, restituendo catalogo 1,
una pubblicazione ed exit 0. L'errore iniziale non è quindi riprodotto: senza lo stdout
originario non è possibile attribuirne la causa in modo certo. Non viene dichiarato risolto
da questo solo tentativo. È stato osservato anche un avviso del launcher Python sul percorso
reale dell'interprete; da solo non impedisce la pubblicazione riuscita.

I checksum del codice e dei lockfile letti durante la prova sono salvati in
`.work/host-proof/baseline-checksums.json`. Sono un riscontro locale datato, non uno SHA Git
e non una certificazione delle modifiche successive.

**Esito:** `G_HOST` verificato per il percorso MCP di assistenza legacy; disponibilità di
una delega nativa minima dimostrata separatamente. La milestone P7 completa e il dispatch
del planner restano soggetti alle verifiche d'integrazione e ai vincoli dell'adapter.

## Aggiornamento v0.3

Tre sessioni Claude Code 2.1.302 distinte hanno esercitato il registry MCP: 27 chiamate totali
con diagnostica, ricerca, cache, piano, prepare, load, risorsa, rifiuto traversal e no-match.
Tre esecuzioni del ponte operatore hanno inoltre osservato la sequenza inizializzazione, tool
`Agent`, task figlio e receipt terminale correlata.

Le receipt non dimostrano che un marker sia stato pre-caricato per ogni skill, né che una lista
di tool venga applicata dal runtime. Entrambi gli esiti restano rispettivamente
`not_verified` e `unknown`; il bridge non è pubblicato come tool MCP e nessun piano del registry
avvia Claude automaticamente. L'osservazione del contesto su corpus reale di 100 skill è locale
al client e non espone token delle skill: non sostiene una stima di riduzione del contesto.
