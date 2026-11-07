# MatteoEye

Programma per Windows e Mac che mostra in diretta le telecamere Blink e permette di
registrare quello che vedono in un file video. Si apre in una finestra sua, non serve
un browser e non serve tenere acceso nessun altro computer: ogni PC accede da solo
all'account Blink.

## Scaricare e usare

Nella cartella principale ci sono i pacchetti pronti:

- `MatteoEye per Windows.zip`: estrai lo zip e fai doppio clic su `Avvia MatteoEye.bat`.
  Non serve installare nulla (Python e ffmpeg sono dentro).
- `MatteoEye per Mac.zip`: estrai lo zip e fai doppio clic su `MatteoEye`
  (oppure `MatteoEye (Mac Intel)` sui Mac con processore Intel).

Dentro ogni zip c'e' un LEGGIMI con i passaggi, compreso come superare l'avviso di
sicurezza la prima volta (il programma non e' firmato da uno sviluppatore registrato).

Al primo avvio si accede con email e password Blink e con il codice che Blink manda
via SMS o email. Restano salvati solo i token di accesso (mai la password), nella
cartella dati dell'utente (`Libreria/Application Support/MatteoEye` sul Mac,
`%APPDATA%\MatteoEye` su Windows), insieme al file di log.

## Funzioni

- **Live**: il video arriva in continuo e parte dal primo fotogramma chiave; in genere
  compare 7-20 secondi dopo il clic, quasi tutti spesi da Blink per svegliare la
  telecamera a batteria.
- **Registra**: salva in un file `.mp4` quello che vede la telecamera; allo stop si
  sceglie dove salvarlo (cartella predefinita: Filmati/Video > MatteoEye).
- **Foto**: scatta una nuova miniatura.
- Schermo intero con `⤢`, Esc per uscire.
- Il programma si chiude da solo pochi minuti dopo che la finestra e' stata chiusa.

## Come funziona

`app.py` e' un piccolo server web (FastAPI) che gira solo sul computer stesso
(127.0.0.1:8787). Usa la libreria [blinkpy](https://github.com/fronzbot/blinkpy)
per parlare con l'API non ufficiale di Blink. Per ogni live:

1. chiede a Blink di avviare la diretta e si collega al server video che Blink indica;
2. riceve i pacchetti MPEG-TS e li passa a due copie di ffmpeg: una produce il flusso
   per la finestra (inviato via WebSocket, riprodotto da `static/mpegts.js`), l'altra
   scrive l'intera sessione in un file temporaneo usato dal tasto Registra;
3. allo stop della registrazione ritaglia dal file di sessione il tratto registrato
   in un `.mp4`, senza ricodificare il video.

La pagina (`static/index.html`) manda un battito ogni 15 secondi: senza battiti il
programma si spegne.

## Avviare dal codice sorgente

Serve Python 3.11 o piu' recente.

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt pywebview
./.venv/bin/python app.py --app      # finestra propria
./.venv/bin/python app.py            # solo browser, dati nella cartella del progetto
./.venv/bin/python app.py --lan      # visibile anche dagli altri dispositivi di casa
```

Sul Mac ci sono anche gli avviatori `Avvia Blink Live*.command` (doppio clic), descritti
in `LEGGIMI.txt`.

## Ricostruire i pacchetti

Dal Mac, con Python 3.13 di python.org installato:

```bash
strumenti/costruisci_mac.sh        # produce "MatteoEye per Mac.zip" (chip Apple + Intel con Rosetta)
strumenti/costruisci_windows.sh    # produce "MatteoEye per Windows.zip" (Python embeddable + pacchetti Windows)
```

## Test

I test usano una telecamera simulata (ffmpeg genera un video di prova), quindi non
servono telecamere vere ne' un account Blink:

```bash
./.venv/bin/python tests/test_ws.py        # flusso live via WebSocket e registrazione, con il server vero
./.venv/bin/python tests/test_webview.py   # riproduzione nella finestra WebKit (come l'app Mac)
./.venv/bin/python tests/test_chrome.py    # riproduzione con Chrome headless (motore di WebView2 su Windows)
```

## Note

- L'API di Blink non e' ufficiale e puo' cambiare; in quel caso aggiornare blinkpy.
- Le telecamere a batteria chiudono la live dopo pochi minuti (come nell'app Blink) e
  ogni live consuma batteria.
- "Camera is offline" e "System is busy" sono risposte di Blink: telecamera spenta o
  server occupati, non errori del programma.
