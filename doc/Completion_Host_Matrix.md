# Matrice host di completamento — 22 ottobre 2026

Superficie: Claude Code locale con profili sperimentali versionati. La versione di
ogni prova deve essere letta dall'evento `system/init` e confrontata con il protocollo;
2.1.305 è soltanto la versione delle prove storiche del 21 ottobre.

| Funzione | Stato attuale | Evidenza disponibile | Primitiva ancora necessaria |
|---|---|---|---|
| Isolamento A_NATIVE / C_FSR | storico verificato; profili nuovi verificati offline | `.work/probe-isolation/REPORT.md`, `test_client_profiles.py` | nuovo preflight locale sulla versione fissata, inclusi tool e contaminazioni |
| B_CURATED esposto nel prompt | materializzazione verificata; misura host unverified | catalogo canonico inserito letteralmente nel prompt, hash persistito | osservazione host omogenea di catalogo/istruzioni/residui; solo file presente non basta |
| Tool MCP disponibili | storico verificato; controllo nuovo offline | init correlato a server connected e lista esatta di 10 nomi | nuovo preflight sul processo reale e sulla configurazione isolata |
| Manuale consegnato in C | verificabile da capture operatore | tool_use/result, riferimento completo uguale, SHA-256 del corpo nel corpus | wire capture di ogni trial reale; emissione server da sola non basta |
| Manuale caricato in A | unverified quando manca corpo osservabile | invocazione Skill è selezione; messaggio Launching non certifica lettura | ricevuta host di consegna con identità e hash del manuale |
| Applicazione delle istruzioni | unknown per solo parser | `applied=[]`, `application_status=unknown` | grader deterministico esterno o revisione umana del task |
| Dispatch automatico del piano | unsupported dall'adapter attuale | `runtime.py` conserva il gate chiuso | canale host attendibile con piano/source/binding revision e lifecycle |
| Preload nei subagent | unsupported dall'adapter attuale | manuali e HMAC operatore non sono attestazione | attestazione host distinta di preload prima dell'esecuzione |
| Enforcement tool dei subagent | unsupported dall'adapter attuale | nessun grant derivato da JSON o self-report | enforcement host con prove negative e correlazione lifecycle |
| Ritiro e cancellazione nativi | unsupported dall'adapter attuale | nessun launcher/fallback nel runtime | API host per dispatch/cancel/withdraw e ricevute verificabili |
| Riduzione catalogo A/C e B/C | unverified, valore null | `/context` non disponibile nel profilo C completo | metodo osservabile omogeneo sui bracci; byte/init/token fatturati non sostituiscono il contesto |

G08 resta chiuso. La diagnosi C08 è completata per il codice disponibile; P7 e
CR06/CR14 restano aperti per le funzioni native prive di attestazione. Non è stata
aggiunta alcuna attivazione nativa. I risultati unsupported descrivono l'adapter
attuale, non una dichiarazione generale su tutte le capacità della piattaforma.

Le prove offline C03/C07 non certificano G03 sulla nuova versione. I profili B senza
MCP sono utili soltanto per controllare l'inserimento del catalogo dichiarativo:
la consegna operativa B richiede configurazione MCP loading-only e riferimenti
revisionati nei descrittori. La allowlist operativa B permette load/resource/diagnostics;
C aggiunge search/plan/prepare. Pubblicazione, discovery, ispezione sorgenti e submit
sono vietati nei trial. Nessun tool filesystem è disponibile nei task dichiarativi.
