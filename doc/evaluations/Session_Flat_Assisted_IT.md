# Prova assistita flat IT nella sessione corrente

Data: 22 ottobre 2026. Stato: `development_assisted`.
Tre richieste italiane, una ricerca e un caricamento per richiesta, mediante FSR CLI
locale e sessioni distinte. Nessun fallback inglese necessario. Le risposte sono
state prodotte in questa sessione Codex dopo la lettura dei manuali selezionati.

L'agente aveva già visto catalogo, contesto e dataset durante l'implementazione:
questa prova è contaminata e descrittiva. Non è un holdout, un confronto A/B/C,
una certificazione G09 o una misura del risparmio di contesto. Non sono stati
lanciati Claude CLI, modelli esterni o chiamate aggiuntive a pagamento.

## 1. Tabella di test Go per una funzione pura

Richiesta: «Proponi una tabella di test Go per input validi, errori e casi limite di
una funzione pura.»

FSR ha restituito cinque candidati; il primo descrive esplicitamente test Go a
tabella, subtest e casi di errore. È stato caricato quel solo manuale,
`golang-testing`, nella sessione `flat-it-go`.

Propongo `ParsePort`: converte una stringa di sole cifre ASCII in una porta tra
1 e 65535. Ammette zeri iniziali; rifiuta spazi e segni, distinguendo sintassi e
valore fuori intervallo. Non effettua I/O e non modifica stato esterno.

| Categoria | Input | Risultato |
|---|---|---|
| Valido ordinario | `8080` | 8080 |
| Limite inferiore | `1` | 1 |
| Limite superiore | `65535` | 65535 |
| Zeri iniziali ammessi | `00080` | 80 |
| Vuoto | stringa vuota | ErrPortSyntax |
| Spazio, segno, carattere estraneo | ` 80`, `+80`, `-1`, `80x` | ErrPortSyntax |
| Cifra non ASCII | `８０` | ErrPortSyntax |
| Fuori intervallo | `0`, `65536`, sequenza numerica molto lunga | ErrPortRange |

Implementazione proposta, `port.go`:

```go
package port

import (
    "errors"
    "strconv"
)

var (
    ErrPortSyntax = errors.New("port must contain ASCII digits only")
    ErrPortRange  = errors.New("port must be between 1 and 65535")
)

func ParsePort(input string) (uint16, error) {
    if input == "" {
        return 0, ErrPortSyntax
    }
    for _, c := range input {
        if c < '0' || c > '9' {
            return 0, ErrPortSyntax
        }
    }
    value, err := strconv.ParseUint(input, 10, 16)
    if err != nil || value == 0 {
        return 0, ErrPortRange
    }
    return uint16(value), nil
}
```

Test proposti, `port_test.go`:

```go
package port

import (
    "errors"
    "testing"
)

func TestParsePort(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        want    uint16
        wantErr error
    }{
        {"ordinary", "8080", 8080, nil},
        {"minimum", "1", 1, nil},
        {"maximum", "65535", 65535, nil},
        {"leading zeros", "00080", 80, nil},
        {"empty", "", 0, ErrPortSyntax},
        {"space", " 80", 0, ErrPortSyntax},
        {"plus", "+80", 0, ErrPortSyntax},
        {"negative", "-1", 0, ErrPortSyntax},
        {"suffix", "80x", 0, ErrPortSyntax},
        {"unicode digits", "８０", 0, ErrPortSyntax},
        {"zero", "0", 0, ErrPortRange},
        {"above maximum", "65536", 0, ErrPortRange},
        {"overflow", "999999999999999999999999999999", 0, ErrPortRange},
    }
    for _, tc := range tests {
        tc := tc
        t.Run(tc.name, func(t *testing.T) {
            t.Parallel()
            got, err := ParsePort(tc.input)
            if !errors.Is(err, tc.wantErr) {
                t.Fatalf("ParsePort(%q) error=%v; want %v", tc.input, err, tc.wantErr)
            }
            if got != tc.want {
                t.Errorf("ParsePort(%q)=%d; want %d", tc.input, got, tc.want)
            }
        })
    }
}
```

Il manuale ha guidato tabella, nomi significativi, `t.Run`, errori verificati e
parallelismo di casi indipendenti. Per un ciclo TDD si scrivono prima i test,
si verifica RED e poi si introduce la funzione. Qui il codice è una proposta:
`go test ./...` non è stato eseguito e nessun PASS Go è dichiarato.

## 2. Router, dipendenze e Pydantic in una piccola API FastAPI

Richiesta: «Descrivi come separare router, dipendenze e validazione Pydantic in una
piccola API FastAPI.»

FSR ha restituito cinque candidati; il primo tratta struttura FastAPI, Pydantic,
iniezione delle dipendenze e router. È stato caricato quel solo manuale,
`fastapi-patterns`, nella sessione `flat-it-fastapi`.

Una struttura sufficiente è:

```text
app/
  main.py
  schemas/items.py
  dependencies.py
  routers/items.py
  services/items.py
```

`main.py` costruisce l'applicazione e include `items.router` con prefisso `/items`.
Gli schemi descrivono contratti di ingresso e uscita: `ItemCreate` può richiedere
un nome di 1–100 caratteri e un prezzo positivo; `ItemResponse` aggiunge l'ID.
In Pydantic v2 si usano `Field` per i limiti e `model_validator` solo per vincoli
fra campi. I campi interni, come costo di acquisto o credenziali, non entrano
nello schema della risposta. La validazione della richiesta avviene prima del
router; un prezzo negativo produce la risposta di validazione HTTP 422.

`dependencies.py` gestisce risorse e contesto per richiesta, per esempio sessione
DB e utente corrente. Una dipendenza DB con `yield` apre la sessione, la consegna
al chiamante e la chiude al termine; eventuale rollback deve avere responsabilità
chiara. Alias tipizzati con `Annotated[..., Depends(...)]` rendono leggibili i
parametri dei router. Autenticazione e autorizzazione restano controlli distinti.

Il router dichiara HTTP, `response_model` e codici di stato; riceve input già
validato e dipendenze iniettate, quindi delega al servizio. Il servizio applica
regole di dominio e gestisce le transazioni. Un nome già esistente, per esempio,
è una regola di dominio/DB: un vincolo univoco e la gestione del conflitto nel
servizio evitano il controllo concorrente «prima cerco, poi inserisco».
Il router traduce l'errore di dominio nel codice HTTP deciso dal contratto.

```python
# schemas/items.py
from pydantic import BaseModel, Field

class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price: float = Field(gt=0, allow_inf_nan=False)

class ItemResponse(ItemCreate):
    id: int
```

Per verificarla: testare gli schemi sui limiti, sovrascrivere la dipendenza DB
con `app.dependency_overrides` nei test HTTP, controllare 201 per input valido,
422 per input invalido e assenza di campi interni nell'output. Usare I/O asincrono
nei router `async def`; chiamate DB sincrone bloccanti richiedono una gestione
coerente con il tipo di handler. Questo è un disegno proposto: non è stata
costruita o eseguita un'API in questa prova.

## 3. Cercare skill prima di crearne una

Richiesta: «Definisci una procedura per cercare skill esistenti localmente e nei
marketplace prima di crearne una nuova.»

FSR ha restituito cinque candidati; il primo descrive esattamente ricerca locale,
marketplace e verifica prima della creazione. È stato caricato quel solo manuale,
`skill-scout`, nella sessione `flat-it-scout`.

1. Scrivere il risultato richiesto, i trigger di attivazione, strumenti/framework
   coinvolti e tre–cinque parole chiave con sinonimi. Conservare questi criteri
   come domanda di ricerca, senza assumere che serva una skill nuova.
2. Cercare prima nel catalogo locale FSR e negli elenchi delle skill installate.
   Valutare nome e descrizione; aprire il manuale solo dei candidati pertinenti.
   Se il catalogo non include i marketplace locali, cercare i loro `SKILL.md`
   nelle radici effettivamente installate, usando `rg --files` e poi `rg -i`
   su nome, descrizione e sinonimi. Registrare le radici consultate.
3. Consultare i marketplace disponibili e poi, se necessario, GitHub/web con poche
   query mirate: workflow + framework + `skill` oppure `SKILL.md`. Distinguere
   «nessun risultato» da «fonte non accessibile». Non installare durante la ricerca.
4. Leggere frontmatter e istruzioni di ogni candidato esterno rilevante. Controllare
   comandi shell inattesi, scritture, rete, credenziali e installazioni; verificare
   manutenzione e compatibilità con il proprio host. Copiare un eventuale fork
   in una nuova cartella o branch e rivedere il diff, preservando l'originale.
5. Ordinare i risultati per corrispondenza al workflow: nome esatto, descrizione
   e sinonimi, presenza locale/marketplace, repository mantenuto, semplice
   menzione web. Limitare la tabella finale a dieci risultati e indicare, per
   ciascuno, fonte, corrispondenza e mancanze concrete.
6. Decidere fra riuso, estensione e creazione. Riutilizzare se copre il bisogno;
   estendere se manca un passo circoscritto; creare quando non esiste un candidato
   sufficientemente vicino oppure l'utente sceglie esplicitamente quel percorso.
   Registrare query e fonti già consultate per rendere la decisione verificabile.

Questa risposta definisce la procedura. Non sono stati interrogati marketplace,
GitHub o servizi esterni e non è stata creata o installata alcuna skill.

## Tracciabilità e limiti della cattura

Registry isolato: `C:/git/FastSkillRetrieval/.work/completion-session-flat-20261022/registry`.
Sorgenti: `C:/git/FastSkillRetrieval/.work/real-eval-fixture-4/sources`.
Artefatto strutturato: `.work/completion-implementation/flat-assisted-it.json`.
Il bundle originale conserva i tre stdout JSON dei load, gli stderr e i rispettivi
SHA-256. I request ID delle search sono registrati nell'artefatto. Le risposte
search complete sono nel transcript del tool della sessione; il primo batch non
ne ha salvato un file stdout separato. Il limite è conservato esplicitamente e
non è stato corretto ripetendo le ricerche.

Il primo load ha salvato il raw correttamente ma la stampa console ha incontrato
un carattere non rappresentabile in cp1252. La ripresa ha riutilizzato quel raw,
senza ripetere il caricamento, e ha completato gli altri due. Le tre risposte
load riportano `content_emitted_not_application_confirmed`: la produzione di
queste risposte mostra uso assistito dei manuali nella sessione, senza attestare
invocazione nativa o qualità con un grader indipendente.
