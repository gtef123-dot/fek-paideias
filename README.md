# 📚 ΦΕΚ Παιδείας

Καθημερινά ενημερωμένη βάση **ΦΕΚ & εγκυκλίων** για την **Πρωτοβάθμια & Δευτεροβάθμια Εκπαίδευση**, με **αυτόματη κατηγοριοποίηση** και αναζήτηση για εκπαιδευτικούς. Τρέχει **εντελώς δωρεάν** σε GitHub Actions + GitHub Pages.

> Ο δάσκαλος γράφει «τι θέλω να βρω» (ή διαλέγει κατηγορία/βαθμίδα) και πάει με τη μία στον σωστό νόμο, με σύνδεσμο στο **επίσημο PDF του ΦΕΚ**.

---

## Πώς δουλεύει (αρχιτεκτονική)

```
  ΚΑΘΕ ΜΕΡΑ (GitHub Actions cron)
  ┌───────────────────────────────────────────────────────────┐
  │ 1. Συλλογή  →  e-nomothesia RSS (νόμοι/ΠΔ/ΥΑ + αριθμός ΦΕΚ) │
  │             →  Διαύγεια OpenData (decisionType + org/date)  │
  │ 2. Ταξινόμηση → κανόνες + Gemini· authority + event-detect  │
  │ 3. Φιλτράρισμα θορύβου (decisionType whitelist + θέμα)      │
  │ 4. Αποθήκευση → data/index.json (ελαφρύ) + data/docs/{year}/│
  │ 5. git commit docs/data  (δημοσίευση + keepalive)           │
  └───────────────────────────────────────────────────────────┘
                              │
                              ▼
   GitHub Pages: φορτώνει ΜΟΝΟ το index· lazy-load ανά έγγραφο
```

- **Πηγές** (όλες δωρεάν, επαληθευμένες): [e-nomothesia.gr](https://www.e-nomothesia.gr) RSS, [Διαύγεια OpenData API](https://diavgeia.gov.gr/opendata), επίσημο PDF από [Εθνικό Τυπογραφείο](https://search.et.gr). Τα κείμενα των ΦΕΚ είναι **δημόσια** (Ν. 2121/1993, άρ. 2§5).
- **Agent**: 2 επίπεδα — (1) ντετερμινιστικοί κανόνες ελληνικών λέξεων-κλειδιών (ρίζες, ώστε να πιάνουν όλες τις κλίσεις), (2) προαιρετικά **Gemini 2.5 Flash** (δωρεάν tier) μόνο για τις δύσκολες περιπτώσεις.
- **Κατηγορίες**: 19 θεματικές (Διορισμοί, Μεταθέσεις, Άδειες, Ειδική Αγωγή, Εξετάσεις…) + άξονες **Βαθμίδα** & **Τύπος πράξης**.

## Δομή

```
fek-paideias/
├── ingester/            # ο daily ingester (Python)
│   ├── config.py        # endpoints, org ids, ρυθμίσεις (one-file fix αν αλλάξει API)
│   ├── taxonomy.py      # κατηγορίες + λέξεις-κλειδιά + normalization
│   ├── classify.py      # κανόνες + Gemini
│   ├── net.py           # ευγενικός HTTP (UA, delay, retries)
│   ├── sources/         # enomothesia.py, diavgeia.py
│   ├── store.py         # SQLite + sharded export (index + per-doc)
│   ├── authority.py     # επίπεδο πηγής / verification / disclaimer
│   ├── events.py        # ανίχνευση τροποποιήσεων/καταργήσεων
│   └── run.py           # entry point:  python -m ingester.run
├── docs/                # στατικό site (GitHub Pages) ← index.html, app.js, style.css
│   └── data/            # index.json (ελαφρύ) + docs/{year}/{doc_id}.json (πλήρη)
├── worker/              # Cloudflare Worker proxy για την AI σύνθεση (κρυφό κλειδί)
├── data/                # τοπική SQLite (δεν ανεβαίνει — χτίζεται από το docs/data)
├── .github/workflows/daily.yml
└── requirements.txt
```

## Τοπική εκτέλεση

```powershell
pip install -r requirements.txt
python -m ingester.run            # γεμίζει το docs/data/ με live δεδομένα
python -m http.server 8125 --directory docs   # άνοιξε http://localhost:8125
```

Για να ενεργοποιήσεις το AI layer του ingester τοπικά: `setx GEMINI_API_KEY "το-κλειδί-σου"` (από το [Google AI Studio](https://aistudio.google.com/apikey), δωρεάν). Η browser σύνθεση χρησιμοποιεί Worker proxy, όχι κλειδί μέσα στη σελίδα.

## Ανέβασμα online (μία φορά)

1. Φτιάξε νέο **public** repository στο GitHub και ανέβασε **τα περιεχόμενα του φακέλου `fek-paideias/`** στη ρίζα του.
2. **Settings → Pages** → Source: *Deploy from a branch* → Branch: `main`, φάκελος: **`/docs`** → Save. Σε ~1 λεπτό το site είναι live στο `https://<user>.github.io/<repo>/`.
3. (Προαιρετικό AI ingest/enrichment) **Settings → Secrets and variables → Actions → New repository secret**: όνομα `GEMINI_API_KEY`, τιμή το δωρεάν κλειδί σου.
4. **Actions** καρτέλα → άνοιξε «Daily ΦΕΚ ingest» → **Run workflow** για να τρέξει η πρώτη συλλογή τώρα. Από εκεί και πέρα τρέχει **μόνο του κάθε μέρα στις ~08:00**.

> ⚠️ Σε public repos, τα προγραμματισμένα workflows **απενεργοποιούνται μετά από 60 μέρες χωρίς commit**. Επειδή ο ingester κάνει commit το `docs/data/` σε κάθε εκτέλεση με αλλαγές, ο μετρητής μηδενίζεται αυτόματα — δεν χρειάζεται τίποτα άλλο.

## Επίπεδο 2 (Σύνθεση AI) — proxy με ΤΟ ΔΙΚΟ ΣΟΥ κλειδί (κρυφό)

Η σύνθεση απάντησης τρέχει στον browser, οπότε για να μπει **μόνο το δικό σου** κλειδί **χωρίς να εκτεθεί**, χρησιμοποιούμε ένα δωρεάν **Cloudflare Worker** (φάκελος `worker/`):

```bash
cd worker
npx wrangler login
npx wrangler secret put GEMINI_API_KEY      # επικόλλησε το κλειδί σου (κρυφό)
npx wrangler deploy                          # αφού ρυθμίσεις το wrangler.toml, δίνει URL workers.dev
```
- Στο `worker/wrangler.toml` βάλε `ALLOWED_ORIGINS = "https://<user>.github.io"` πριν το deploy. Άδειο `ALLOWED_ORIGINS` μπλοκάρεται επίτηδες.
- Προαιρετικά ρύθμισε `MAX_REQUESTS_PER_MINUTE` για το δωρεάν quota. Το Worker έχει best-effort per-IP rate limit· για βαριά δημόσια χρήση πρόσθεσε Turnstile/auth.
- Στο `docs/config.js` βάλε `SYNTH_ENDPOINT: "https://fek-paideias-synth.<account>.workers.dev"`.

Έτσι: κανένας δάσκαλος δεν βάζει κλειδί, και το δικό σου μένει **secret στον server**.
**Τοπική δοκιμή** χωρίς Worker: στην κονσόλα του browser τρέξε `localStorage.setItem('fek_dev_key','ΤΟ_ΚΛΕΙΔΙ')` (ενεργό μόνο σε localhost).

## Δοκιμές & υγεία

- `python -m ingester.selftest` — ελέγχει ταξινόμηση, corpora και golden retrieval queries (τρέχει & στο CI πριν το ingest).
- Ο ingester επιστρέφει σφάλμα αν βγάλει 0 εγγραφές (δεν αντικαθιστά καλά δεδομένα με κενά).

## Επιφυλάξεις (ειλικρινά)

- Το API του et.gr (επίσημο PDF) είναι **ανεπίσημο**· αν αλλάξει, διορθώνεται στο `config.py`.
- Η κατηγοριοποίηση & η σύνθεση είναι **αυτόματες** — για νομική βεβαιότητα, ανοίξτε πάντα το επίσημο ΦΕΚ.
- **«Ισχύει/καταργήθηκε»:** το et.gr ΔΕΝ εκθέτει αξιόπιστο «γράφημα σχέσεων», οπότε το πεδίο `status` στα seeds είναι **χειροκίνητο/ευρετικό** — όχι πλήρως αυτόματο. (π.χ. ΦΕΚ 491/Β/2021 σημειωμένο ΚΑΤΑΡΓΗΘΕΝ από 5387/Β/2024.)
- **Κάλυψη:** η βάση μαζεύει **από εδώ και μπρος** (+ 22 seeds + 3 cards). Πλήρες ιστορικό αρχείο = ξεχωριστό έργο.
- **Αναζήτηση:** keyword + χάρτης συνωνύμων (καθομιλουμένη→νομικοί όροι), όχι πλήρη embeddings.
- Τα νούμερα στα knowledge cards προέρχονται από **κωδικοποιημένους οδηγούς** (status `ΟΔΗΓΟΣ`) — διασταυρώστε με το πρωτογενές ΦΕΚ για κρίσιμες αποφάσεις.
- Το cron του GitHub είναι **best-effort** στον χρόνο· στο **δωρεάν** Gemini τα prompts μπορεί να χρησιμοποιηθούν για εκπαίδευση (τα ΦΕΚ είναι ήδη δημόσια).
