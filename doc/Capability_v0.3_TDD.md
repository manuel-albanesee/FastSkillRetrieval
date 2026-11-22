# Verifica TDD v0.3

Data: 18 ottobre 2026. Le correzioni hanno seguito il ciclo red/green sui contratti che erano
rimasti aperti, senza attribuire valore di prodotto a una prova sintetica.

| Caso inizialmente rosso | Correzione | Verifica verde |
|---|---|---|
| Mancava `corpus_fingerprint` richiesto dai test di valutazione. | Impronta separata del corpus e della review; approvazione legata a entrambe. | `tests/test_evaluation.py`: 14 passed. |
| Un preload con risorsa poteva essere presentato come preload nativo. | Il bridge rifiuta quel caso; il test usa un preload semplice, materializzabile. | `tests/test_host_bridge.py` e `tests/test_native_integration.py`: 11 passed. |
| Il client Claude era invocato con opzioni incompatibili per una prova locale. | Configurazione MCP vuota esplicita, modalità ristretta e nessun tool diretto. | `tests/test_claude_context.py`: 7 passed; osservazione reale salvata sotto `.work`. |
| La pubblicazione di 5.000 elementi in batch da 100 superava la deadline. | Batch da 10 e ripresa esplicita, senza ricreare o alterare la fixture. | La ripresa ha pubblicato 5.000 elementi; p95 della ricerca: 64,91 ms su 100 campioni. |

La suite completa ha concluso con **198 passed, 1 skipped** e copertura istruzioni del
**86%**. `ruff check src tests scripts` e `ruff format --check src tests scripts` sono parte
della chiusura. L'esperimento 100/20 non è stato avviato: la proposta richiede ancora revisione
e approvazione umana degli attesi.
