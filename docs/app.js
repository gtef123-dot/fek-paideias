// ΦΕΚ Παιδείας — static client. Loads records.json and provides guided
// browsing (categories + level/type/source filters) plus free-text search.

const state = {
  all: [],
  meta: null,
  q: "",
  level: "",
  category: "",
  type: "",
  source: "",
  role: "",
  clarify: "",   // chosen clarification option for Level-2 synthesis
  days: "",      // date filter: "" = all, else last N days
  limit: 60,     // pagination page size
};

// Role profiles: selecting a role narrows results to what matters for that role
// (relevant categories and/or level). Category names must match taxonomy.py.
const ROLES = {
  "": { label: "Είμαι… (όλοι οι ρόλοι)", levels: [], categories: [] },
  daskalos: { label: "Δάσκαλος (Πρωτοβάθμια)", levels: ["Πρωτοβάθμια"], categories: [] },
  kathigitis: { label: "Καθηγητής (Δευτεροβάθμια)", levels: ["Δευτεροβάθμια"], categories: [] },
  nipiagogos: { label: "Νηπιαγωγός", levels: ["Πρωτοβάθμια"], categories: [] },
  eae: { label: "Ειδική Αγωγή (ΕΕΠ/ΕΒΠ)", levels: [], categories: ["Ειδική Αγωγή & Εκπαίδευση (ΕΑΕ)"] },
  diefthintis: {
    label: "Διευθυντής / Στέλεχος", levels: [],
    categories: ["Στελέχη / Επιλογή Διευθυντών", "Οργάνωση & Διοίκηση Σχολείων",
                 "Αξιολόγηση Εκπαιδευτικών & Σχ. Μονάδων"],
  },
  anaplirotis: {
    label: "Αναπληρωτής", levels: [],
    categories: ["Προσλήψεις Αναπληρωτών", "Διορισμοί Μονίμων (ΑΣΕΠ)"],
  },
  epal: {
    label: "ΕΠΑΛ / Επαγγελματικής", levels: ["Δευτεροβάθμια"],
    categories: ["ΕΠΑΛ / Επαγγελματική Εκπαίδευση"],
  },
};

// Greek-aware normalization — must mirror ingester/taxonomy.py::normalize.
function norm(s) {
  if (!s) return "";
  return s
    .normalize("NFD").replace(/[̀-ͯ]/g, "")  // strip accents
    .toLowerCase()
    .replace(/ς/g, "σ")                                 // unify final sigma
    .replace(/\./g, "")                                 // join acronym dots
    .replace(/[^0-9a-zα-ω\s]/g, " ")                    // punctuation -> space
    .replace(/\s+/g, " ").trim();
}

let lastRows = [];  // rows currently shown — fed to Level-2 synthesis

// Intent → legal-term expansion: map everyday words a teacher might type to the
// legal stems used in the documents, so natural questions retrieve the right ΦΕΚ.
const SYNONYMS = [
  { k: ["γεννησ", "τοκετ", "εγκυ", "εγκυμον", "μωρο", "γεννηθ", "γεννησα"], add: ["κυησ", "μητροτητ", "λοχει", "ανατροφ", "πατροτητ"] },
  { k: ["αρρωστ", "ασθεν", "αναρρωσ", "αδιαθ", "γριπ"], add: ["αναρρωτικ"] },
  { k: ["κηδει", "πενθ", "χασα", "θανατ", "απεβιωσ", "πεθαν"], add: ["πενθ"] },
  { k: ["λεφτα", "πληρωμ", "πληρωθ", "χρηματ", "αμοιβ", "μισθο"], add: ["μισθολογ", "αποδοχ", "αποζημιωσ"] },
  { k: ["μετακινηθ", "αλλαγη σχολει", "φυγω απο", "μεταφερθ"], add: ["μεταθεσ", "αποσπασ", "τοποθετησ"] },
  { k: ["μεσα στην ταξη", "συνεκπαιδ", "μαζι με τον δασκαλο", "δευτερος δασκαλ"], add: ["συνδιδασκ", "παραλληλ στηριξ", "ειδικ αγωγ"] },
  { k: ["δευτερο σχολει", "δυο σχολει", "αλλο σχολει", "συμπληρων"], add: ["συμπληρωσ", "διαθεσ", "ωραριο"] },
  { k: ["διοριστηκ", "μονιμοποιηθ", "εγινα μονιμ", "νεοδιοριστ"], add: ["διορισμ", "μονιμοποιησ"] },
  { k: ["προσληφθ", "αναπληρωτ", "οπσυδ"], add: ["προσληψ", "αναπληρωτ"] },
  { k: ["πανελλ", "βασ", "μηχανογραφ", "υποψηφι"], add: ["εξετασ", "πανελλαδικ", "εισακτε"] },
  { k: ["απουσι", "λειψω", "αδει"], add: ["αδει"] },
];

const STOP_TERMS = new Set([
  "να", "για", "τον", "την", "το", "του", "τη", "της", "των", "τα", "οι", "ο", "η",
  "μου", "σου", "μασ", "σασ", "και", "σε", "στο", "στη", "στην", "στουσ", "στισ",
  "με", "απο", "ως", "αν", "τι", "πωσ", "ποσο", "ποια", "ποιο", "ποιοσ", "ποιαν",
  "ειμαι", "εχω", "θελω", "μπορω", "παιρνω", "δικαιουμαι", "ισχυει", "μηπωσ",
]);

function searchTerms(normQ) {
  let terms = normQ.split(" ").filter((t) =>
    t && !STOP_TERMS.has(t) && (t.length >= 3 || /\d/.test(t)));
  // Drop bare year tokens (e.g. "2026") when combined with words — they match
  // almost every date and drown out the real query.
  if (terms.length > 1) terms = terms.filter((t) => !/^(19|20)\d{2}$/.test(t));
  return expandSynonyms(terms, normQ).filter((t) =>
    t && !STOP_TERMS.has(t) && (t.length >= 3 || /\d/.test(t)));
}

function expandSynonyms(terms, normQ) {
  const extra = new Set(terms);
  for (const s of SYNONYMS) if (s.k.some((kw) => normQ.includes(kw))) s.add.forEach((a) => extra.add(a));
  return [...extra];
}

const $ = (sel) => document.querySelector(sel);

// Lazy-load a document's FULL content (summary_ai, articles, excerpts) on demand.
// Cached on the index entry so it's fetched at most once.
async function loadDoc(entry) {
  if (entry._full) return entry._full;
  try {
    const res = await fetch(`data/docs/${entry.year || "ref"}/${entry.doc_id}.json`,
                            { cache: "force-cache" });
    if (res.ok) { entry._full = await res.json(); return entry._full; }
  } catch (e) { /* offline / missing → fall back to index fields */ }
  return null;
}

async function boot() {
  try {
    // Load ONLY the lightweight index. Full per-doc content is lazy-loaded on demand.
    const res = await fetch("data/index.json", { cache: "no-store" });
    const data = await res.json();
    state.all = data.records || [];
    state.meta = data.meta || {};
    // Precompute field-specific search haystacks from the (light) index fields.
    for (const r of state.all) {
      r._titleHay = norm(r.title);
      r._summaryHay = norm([r.summary, r.summary_ai].join(" "));
      r._detailHay = norm([(r.keywords || []).join(" "), (r.articles || []).join(" "),
                           (r.excerpts || []).join(" ")].join(" "));
      r._tagHay = norm([(r.categories || []).join(" "), (r.levels || []).join(" "), r.doc_type].join(" "));
      r._idHay = norm([r.fek_label, r.ada].join(" "));
      r._hay = [r._titleHay, r._summaryHay, r._detailHay, r._tagHay, r._idHay].join(" ");
    }
  } catch (e) {
    $("#results").innerHTML = `<div class="empty">Σφάλμα φόρτωσης δεδομένων (${e}).</div>`;
    return;
  }
  buildTypeFilter();
  buildRoleFilter();
  buildCategories();
  wireEvents();
  $("#updated").textContent = state.meta.updated_at ? `Ενημέρωση: ${state.meta.updated_at}` : "";
  renderHealth();
  render();
}

// System health: prominent banner if a source broke (per-source freshness +
// canary + shape), else a subtle "sources OK" line. Catches silent failures.
function renderHealth() {
  const box = $("#health");
  if (!box) return;
  const h = (state.meta && state.meta.health) || {};
  const probs = h.problems || [];
  if (probs.length) {
    box.className = "health bad";
    box.innerHTML = `⚠ Πρόβλημα ενημέρωσης πηγών: ${probs.map(esc).join(" · ")}` +
      `<span class="hmeta"> (έλεγχος: ${esc(h.checked_at || "")})</span>`;
    box.hidden = false;
  } else if (h.checked_at) {
    box.className = "health ok";
    const c = h.diavgeia_canary || {};
    box.innerHTML = `✓ Πηγές ενεργές · ${esc(String(h.e_nomothesia_fetched ?? "?"))} νομοθεσία, ` +
      `${esc(String(h.diavgeia_orgs_with_results ?? "?"))} φορείς Διαύγειας` +
      `${c.total ? ` (canary ${esc(String(c.total))})` : ""} · ` +
      `${esc(String(h.new_this_run ?? 0))} νέα τελευταία ενημέρωση`;
    box.hidden = false;
  } else {
    box.hidden = true;
  }
}

function buildTypeFilter() {
  const sel = $("#typeFilter");
  for (const [name, n] of (state.meta.doc_types || [])) {
    const opt = document.createElement("option");
    opt.value = name; opt.textContent = `${name} (${n})`;
    sel.appendChild(opt);
  }
}

function buildRoleFilter() {
  const sel = $("#roleFilter");
  for (const [id, role] of Object.entries(ROLES)) {
    const opt = document.createElement("option");
    opt.value = id; opt.textContent = role.label;
    sel.appendChild(opt);
  }
}

function buildCategories() {
  const box = $("#categories");
  box.innerHTML = "";
  for (const [name, n] of (state.meta.categories || [])) {
    const chip = document.createElement("button");
    chip.className = "cat-chip" + (state.category === name ? " active" : "");
    chip.innerHTML = `${name} <span class="n">${n}</span>`;
    chip.onclick = () => {
      state.category = state.category === name ? "" : name;
      buildCategories();
      render();
    };
    box.appendChild(chip);
  }
}

function wireEvents() {
  const q = $("#q");
  q.addEventListener("input", () => {
    state.q = q.value;
    state.clarify = "";  // new query → fresh synthesis context
    $("#clear").classList.toggle("show", q.value.length > 0);
    render();
  });
  $("#clear").onclick = () => { q.value = ""; state.q = ""; $("#clear").classList.remove("show"); render(); };

  for (const btn of document.querySelectorAll(".lvl")) {
    btn.onclick = () => {
      document.querySelectorAll(".lvl").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.level = btn.dataset.level;
      render();
    };
  }
  $("#typeFilter").onchange = (e) => { state.type = e.target.value; render(); };
  $("#sourceFilter").onchange = (e) => { state.source = e.target.value; render(); };
  $("#roleFilter").onchange = (e) => { state.role = e.target.value; render(); };
  $("#dateFilter").onchange = (e) => { state.days = e.target.value; render(); };
}

function matches(r) {
  if (state.category && !(r.categories || []).includes(state.category)) return false;
  if (state.level) {
    const lv = r.levels || [];
    if (!lv.includes(state.level) && !lv.includes("Όλες / Γενικό")) return false;
  }
  if (state.type && r.doc_type !== state.type) return false;
  if (state.source && r.source !== state.source) return false;
  // Date filter (skips evergreen seed/knowledge so reference law always shows).
  if (state._cutoff && r.source !== "seed" && r.source !== "knowledge" &&
      (r.date || "") < state._cutoff) return false;

  // Role profile: narrow to the role's relevant level + categories.
  const role = ROLES[state.role] || ROLES[""];
  if (role.levels.length) {
    const lv = r.levels || [];
    if (!role.levels.some((l) => lv.includes(l)) && !lv.includes("Όλες / Γενικό")) return false;
  }
  if (role.categories.length && !(r.categories || []).some((c) => role.categories.includes(c)))
    return false;

  return true;
}

function searchScore(r, terms) {
  if (!terms.length) return 1;
  let score = 0;
  let hits = 0;
  for (const t of terms) {
    let termScore = 0;
    if (r._titleHay.includes(t)) termScore += 4;
    if (r._tagHay.includes(t)) termScore += 3;
    if (r._detailHay.includes(t)) termScore += 3;
    if (r._summaryHay.includes(t)) termScore += 2;
    if (r._idHay.includes(t)) termScore += 1;
    if (termScore) { hits += 1; score += termScore; }
  }
  if (!hits) return 0;
  // Authority ranking (primary_law > official_circular > diavgeia_decision >
  // official_guide > secondary_guide > unknown) — a moderate additive so strong
  // text relevance still wins, but authoritative sources break ties upward.
  score += (AUTH_RANK[r.authority_level] || 1) * 1.5;
  if (r.source === "knowledge") score += 4;   // curated concrete-answer cards stay competitive
  if (r.enriched) score += 2;
  return score;
}

const AUTH_RANK = {
  primary_law: 6, official_circular: 5, diavgeia_decision: 4,
  official_guide: 3, secondary_guide: 2, unknown: 1,
};

const compactNorm = (s) => norm(s).replace(/\s+/g, "");
const idCore = (raw) => raw.trim().replace(/^(ΑΔΑ|ΦΕΚ|αδα|φεκ)\s+/i, "").trim();
// ΦΕΚ "number/τεύχος" key (ignores the date): "ΦΕΚ 3358/Β/12-6-2026" -> "3358β".
const fekKey = (s) => {
  const m = (s || "").match(/(\d+)\s*\/\s*([Α-Ωα-ωA-Za-z]+)/);
  return m ? m[1] + m[2].toLowerCase() : "";
};

function renderRows(rows) {
  lastRows = rows;
  const results = $("#results");
  results.innerHTML = "";
  $("#empty").hidden = rows.length > 0;
  $("#count").textContent = `${rows.length} ${rows.length === 1 ? "αποτέλεσμα" : "αποτελέσματα"}`;
  const frag = document.createDocumentFragment();
  const shown = Math.min(state.limit, rows.length);
  for (const r of rows.slice(0, shown)) frag.appendChild(card(r));
  results.appendChild(frag);
  if (rows.length > shown) {
    const more = document.createElement("button");
    more.className = "btn ghost more-btn";
    more.textContent = `Δείξε περισσότερα (+${Math.min(60, rows.length - shown)})`;
    more.onclick = () => { state.limit += 60; renderRows(lastRows); };
    results.appendChild(more);
  }
  renderSynth();
}

// ── Level 2: on-demand AI synthesis (1 request, only when the button is hit) ──
function renderSynth() {
  const box = $("#synth");
  if (!box) return;
  const q = state.q.trim();
  if (!q || lastRows.length === 0) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML = `
    <div class="synth-bar">
      <button id="synthBtn" class="btn primary">✨ Σύνθεση απάντησης από αυτά τα ΦΕΚ</button>
      <span class="synth-hint">Φτιάχνει απάντηση με πηγές από τα παραπάνω ΦΕΚ</span>
    </div>
    <div id="synthOut" class="synth-out" hidden></div>`;
  $("#synthBtn").onclick = () => { state.clarify = ""; synthesize(); };
}

async function synthesize() {
  const docs = lastRows.slice(0, 8);
  if (!docs.length) return;
  const out = $("#synthOut");
  out.hidden = false;
  out.innerHTML = `<div class="loading">⏳ Σύνθεση απάντησης…</div>`;

  // Lazy-load full content (articles/excerpts/summary_ai) for the chosen docs.
  const fulls = await Promise.all(docs.map((d) => loadDoc(d)));
  const ctx = docs.map((r, i) => {
    const f = fulls[i] || r;
    const id = r.fek_label || (r.ada ? "ΑΔΑ " + r.ada : "");
    const s = (f.summary_ai && f.summary_ai.trim()) ? f.summary_ai : (f.summary || r.summary || "");
    const arts = (f.articles || []).slice(0, 12).join(" | ");
    const ex = (f.excerpts || []).join(" / ");
    const st = r.status ? ` [κατάσταση: ${r.status}]` : "";
    const auth = r.authority_level ? ` [πηγή: ${r.authority_level}]` : "";
    return `[${i + 1}] ${id} — ${r.title}${st}${auth}\nΠερίληψη: ${s}` +
           (arts ? `\nΣημεία/άρθρα: ${arts}` : "") + (ex ? `\nΑποσπάσματα: ${ex}` : "");
  }).join("\n\n");

  // Authority gate: if the sources are only guides (no primary law/circular),
  // the answer must NOT be stated as settled law.
  const hasPrimary = docs.some((d) => d.authority_level === "primary_law" ||
                                      d.authority_level === "official_circular");
  const guideOnly = !hasPrimary && docs.some((d) => d.authority_level === "official_guide" ||
                                                    d.authority_level === "secondary_guide");
  const guideNote = "Η πληροφορία προέρχεται από οδηγό και χρειάζεται διασταύρωση με πρωτογενή πηγή.";

  const decided = state.clarify
    ? `\n\nΟ ΧΡΗΣΤΗΣ ΔΙΕΥΚΡΙΝΙΣΕ: "${state.clarify}". Δώσε ΟΡΙΣΤΙΚΗ, συγκεκριμένη απάντηση ` +
      `(mode="answer") για ΑΥΤΗ την εκδοχή — ΜΗΝ ξαναρωτήσεις.`
    : "";

  const prompt =
    `Είσαι βοηθός ελληνικής εκπαιδευτικής νομοθεσίας (μόνιμοι, αναπληρωτές ΕΣΠΑ/ΙΔΟΧ, ` +
    `ΕΑΕ, ΕΕΠ/ΕΒΠ). Δεν είσαι δικηγόρος.\n\n` +
    `Ο/Η εκπαιδευτικός ρωτά: "${state.q.trim()}"${decided}\n\n` +
    `Επίστρεψε ΑΥΣΤΗΡΑ JSON:\n` +
    `{"mode":"clarify"|"answer","question":"...","options":["...","..."],"answer_md":"..."}\n\n` +
    `ΚΑΝΟΝΕΣ:\n` +
    `• mode="clarify" ΜΟΝΟ αν υπάρχει πολύσημος όρος που αλλάζει ριζικά την απάντηση ` +
    `(π.χ. «κάθε μέρα μάθημα στην τάξη» ΕΑΕ → ίσως «συνδιδασκαλία»; «δουλέψω εκεί» → ` +
    `αποκλειστικά εκεί Ή μέρος ωραρίου;). Τότε δώσε σύντομο question + 2-4 σαφείς options. ` +
    `Αν ο χρήστης ΗΔΗ διευκρίνισε, μην ξαναρωτάς.\n` +
    `• mode="answer": γράψε answer_md σε MARKDOWN. Αν είναι λίστα ομοειδών (π.χ. άδειες), ` +
    `ΥΠΟΧΡΕΩΤΙΚΑ **πίνακας** «| Στοιχείο | Ημέρες/Δικαίωμα | Προϋποθέσεις | Πηγή |».\n` +
    `• ΔΩΣΕ ΣΥΓΚΕΚΡΙΜΕΝΟΥΣ ΑΡΙΘΜΟΥΣ ημερών/προθεσμιών όπως υπάρχουν στα ΕΓΓΡΑΦΑ ` +
    `(π.χ. «7 εργάσιμες», «14 ημέρες»). ΜΗΝ γράφεις αόριστα «ορίζονται στον οδηγό».\n` +
    `• Στήριξε κάθε σημείο με [1],[2]… προς τα ΕΓΓΡΑΦΑ. Μη στηρίζεσαι σε [κατάσταση: ` +
    `ΚΑΤΑΡΓΗΘΕΝ]. Αν λείπει στοιχείο, γράψε «προς επιβεβαίωση» — ΜΗΝ επινοείς.\n` +
    `• Κλείσε με σύσταση επιβεβαίωσης από την οικεία Διεύθυνση Εκπαίδευσης.\n` +
    (guideOnly
      ? `• ΠΡΟΣΟΧΗ: οι πηγές είναι ΟΔΗΓΟΙ (όχι πρωτογενής νόμος/εγκύκλιος). ΜΗΝ ` +
        `διατυπώσεις την απάντηση ως βέβαιο νόμο· ΞΕΚΙΝΑ το answer_md με τη φράση: ` +
        `"${guideNote}"\n`
      : "") +
    `\nΕΓΓΡΑΦΑ:\n` + ctx;

  const errHtml = (s) => `<div class="err">Σφάλμα AI (${s}). ` +
    (s === 429 ? "Υπέρβαση ορίου — δοκίμασε σε λίγο." :
     s === 400 || s === 403 ? "Πρόβλημα με το κλειδί/πρόσβαση του proxy." : "") + `</div>`;
  const cfg = window.FEK_CONFIG || {};
  const isDev = ["localhost", "127.0.0.1"].includes(location.hostname);
  const devKey = isDev ? localStorage.getItem("fek_dev_key") : null;

  try {
    let text;
    if (cfg.SYNTH_ENDPOINT) {
      // Production: the Cloudflare Worker holds the key; we only send the prompt.
      const res = await fetch(cfg.SYNTH_ENDPOINT, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // Surface the Worker's clean error (e.g. SERVER_CONFIG_ERROR) instead of a bare 500.
        out.innerHTML = data.error
          ? `<div class="err">Σφάλμα proxy: ${esc(data.error)}${data.error_code ? ` (${esc(data.error_code)})` : ""}</div>`
          : errHtml(data.upstream || res.status);
        return;
      }
      text = data.text || "{}";
    } else if (devKey) {
      // Local dev only: direct call with a key kept in localStorage('fek_dev_key').
      const res = await fetch(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" +
        encodeURIComponent(devKey),
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }],
            generationConfig: { temperature: 0, responseMimeType: "application/json" } }) });
      if (!res.ok) { out.innerHTML = errHtml(res.status); return; }
      const data = await res.json();
      text = data?.candidates?.[0]?.content?.parts?.[0]?.text || "{}";
    } else {
      out.innerHTML = `<div class="err">Η AI-σύνθεση δεν είναι ρυθμισμένη ακόμη — ` +
        `όρισε το <code>SYNTH_ENDPOINT</code> (Cloudflare Worker) στο config.js. Δες το README.</div>`;
      return;
    }
    let obj; try { obj = JSON.parse(text); } catch { obj = { mode: "answer", answer_md: text }; }

    // Clarify mode → render the question + clickable option buttons.
    if (obj.mode === "clarify" && Array.isArray(obj.options) && obj.options.length) {
      const opts = obj.options.map((o, i) =>
        `<button class="btn ghost opt" data-opt="${esc(o)}">${esc(o)}</button>`).join("");
      out.innerHTML =
        `<div class="answer"><p><strong>${esc(obj.question || "Διευκρίνισε:")}</strong></p>` +
        `<div class="opts">${opts}</div></div>`;
      out.querySelectorAll(".opt").forEach((b) => {
        b.onclick = () => { state.clarify = b.dataset.opt; synthesize(); };
      });
      return;
    }

    const sources = docs.map((r, i) => {
      const id = r.fek_label || (r.ada ? "ΑΔΑ " + r.ada : r.title);
      const link = r.official_url || r.source_url || "#";
      return `<li>[${i + 1}] <a href="${esc(link)}" target="_blank" rel="noopener">${esc(id)}</a> — ${esc(trim(r.title || "", 80))}</li>`;
    }).join("");
    const clarNote = state.clarify
      ? `<p class="chosen">Επιλογή: <strong>${esc(state.clarify)}</strong> · <a href="#" id="reSynth">αλλαγή</a></p>` : "";
    const guideBanner = guideOnly ? `<p class="change warn">ℹ ${esc(guideNote)}</p>` : "";
    out.innerHTML =
      clarNote + guideBanner +
      `<div class="answer">${mdToHtml(obj.answer_md || "(κενή απάντηση)")}</div>` +
      `<div class="sources"><strong>Πηγές:</strong><ol>${sources}</ol></div>` +
      `<p class="disclaimer">Ενημερωτικό, όχι νομική συμβουλή — επιβεβαιώστε με την οικεία Διεύθυνση Εκπαίδευσης.</p>`;
    const re = $("#reSynth");
    if (re) re.onclick = (e) => { e.preventDefault(); state.clarify = ""; synthesize(); };
  } catch (e) {
    out.innerHTML = `<div class="err">Αποτυχία κλήσης AI (δίκτυο/CORS): ${esc(String(e))}</div>`;
  }
}

function render() {
  state.limit = 60;  // reset pagination on every new query/filter
  state._cutoff = state.days
    ? new Date(Date.now() - Number(state.days) * 86400000).toISOString().slice(0, 10) : "";
  // ── Identifier (ΑΔΑ / ΦΕΚ) lookup: surface the exact act first ──
  // Match by the COMPACTED code (all punctuation/spaces stripped), so it is
  // robust to how the separator was typed or pasted (ASCII "-", en-dash "–",
  // "/", spaces) and to a leading "ΑΔΑ "/"ΦΕΚ ". Codes are distinctive enough
  // that normal words never collide (we still require a letter + length ≥ 6).
  const rawId = idCore(state.q);
  const q = compactNorm(rawId);
  const fkq = fekKey(rawId);
  const looksId =
    (/[-–—]/.test(rawId) && /\d/.test(q)) ||              // ΑΔΑ-style: any dash + a digit
    !!fkq ||                                              // ΦΕΚ ref: number/τεύχος (π.χ. 3358/Β)
    (q.length >= 8 && /\d/.test(q) && !/\s/.test(rawId)); // long alphanumeric code
  if (looksId && (q.length >= 4 || fkq)) {
    const hits = state.all
      .filter((r) => {
        const ada = compactNorm(r.ada);
        if (ada && q.length >= 6 && ada.includes(q)) return true;     // ΑΔΑ substring
        if (fkq && fekKey(r.fek_label) === fkq) return true;          // ΦΕΚ number+τεύχος
        const fek = compactNorm(r.fek_label);
        return fek && q.length >= 4 && fek.includes(q);               // ΦΕΚ compact substring
      })
      .sort((a, b) => {
        const exact = (r) =>
          (compactNorm(r.ada) === q || fekKey(r.fek_label) === fkq) ? 0 : 1;
        return exact(a) - exact(b);  // exact id match first
      });
    if (hits.length) { renderRows(hits); return; }
    // no id match found → fall through to normal text search
  }

  const normQ = norm(state.q);
  const terms = searchTerms(normQ);
  let rows = state.all.filter(matches);
  if (terms.length) {
    rows = rows
      .map((r) => ({ r, s: searchScore(r, terms) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s || (b.r.date || "").localeCompare(a.r.date || ""))
      .map((x) => x.r);
  } else {
    rows = rows.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  }
  renderRows(rows);
}

function card(r) {
  const el = document.createElement("article");
  el.className = "card" + (r.source === "diavgeia" ? " diavgeia" : "");

  const idBadge = r.fek_label
    ? `<span class="badge fek">${esc(r.fek_label)}</span>`
    : (r.ada ? `<span class="badge ada">ΑΔΑ ${esc(r.ada)}</span>` : "");
  const aiMark = r.enriched ? `<span class="badge ai" title="Αυτόματη ανάλυση AI">✨ AI</span>` : "";
  const stClass = { "ΚΑΤΑΡΓΗΘΕΝ": "dead", "ΠΡΟΣΦΑΤΗ ΑΛΛΑΓΗ": "new", "ΟΔΗΓΟΣ": "guide" };
  // Never assert "ΙΣΧΥΟΝ" (we cannot know in-force state) — only show informative badges.
  const statusBadge = (r.status && stClass[r.status])
    ? `<span class="badge st-${stClass[r.status]}">${esc(r.status)}</span>` : "";

  // Event-based change note (instead of claiming "in force"): amendment warning,
  // else an honest "no change detected up to [last check]" for tracked laws.
  let changeNote = "";
  const aff = r.affected_by || [];
  if (aff.length) {
    const links = aff.map((a) => a.url
      ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.label || "σχετική πράξη")}</a>`
      : esc(a.label || "")).join(" · ");
    changeNote = `<p class="change warn">⚠ Πιθανώς θιγμένο (τροποποίηση/κατάργηση) — απαιτεί έλεγχο: ${links}</p>`;
  } else if (r.status === "ΙΣΧΥΟΝ" || r.status === "ΠΡΟΣΦΑΤΗ ΑΛΛΑΓΗ") {
    const upto = (state.meta && state.meta.updated_at) || "";
    changeNote = `<p class="change ok">Δεν εντοπίστηκε κατάργηση/τροποποίηση έως ${esc(upto)} · επιβεβαιώστε στο επίσημο ΦΕΚ</p>`;
  }

  const tags = (r.categories || []).map((c) =>
    `<span class="tag" data-cat="${esc(c)}">${esc(c)}</span>`).join("");
  const levels = (r.levels || []).filter((l) => l !== "Όλες / Γενικό")
    .map((l) => `<span class="tag lvl-tag">${esc(l)}</span>`).join("");
  const kw = (r.keywords || []).map((k) => `<span class="kw">${esc(k)}</span>`).join("");

  // The index already stores the best (AI) summary, truncated.
  const summary = r.summary || "";

  // Authority-of-source badge.
  const AUTH = {
    primary_law: ["Πρωτογενής νόμος", "prim"], official_circular: ["Επίσημη εγκύκλιος", "circ"],
    diavgeia_decision: ["Πράξη Διαύγειας", "dia"], official_guide: ["Επίσημος οδηγός", "guide"],
    secondary_guide: ["Δευτερεύων οδηγός", "guide"],
  };
  const av = AUTH[r.authority_level];
  const authBadge = av ? `<span class="badge auth auth-${av[1]}" title="Επίπεδο πηγής">${av[0]}</span>` : "";

  // Articles & excerpts live in the per-doc file → lazy-load when expanded.
  const details = r.enriched
    ? `<details class="more"><summary>Σημαντικά σημεία &amp; αποσπάσματα</summary>` +
      `<div class="more-body"><span class="muted">…</span></div></details>`
    : "";

  const actions = [];
  if (r.official_url)
    actions.push(`<a class="btn primary" href="${esc(r.official_url)}" target="_blank" rel="noopener">📄 ${r.fek_label ? "Επίσημο ΦΕΚ (PDF)" : "Έγγραφο (PDF)"}</a>`);
  if (r.source_url)
    actions.push(`<a class="btn ghost" href="${esc(r.source_url)}" target="_blank" rel="noopener">🔗 Πηγή</a>`);

  el.innerHTML = `
    <div class="card-top">
      <span class="badge">${esc(r.doc_type || "Νομοθεσία")}</span>
      ${idBadge}${authBadge}${statusBadge}${aiMark}
      <span class="date">${esc(r.date || "")}</span>
    </div>
    <h3>${esc(r.title || "(χωρίς τίτλο)")}</h3>
    ${summary && summary !== r.title ? `<p class="summary">${esc(trim(summary, 320))}</p>` : ""}
    <div class="tags">${tags}${levels}</div>
    ${kw ? `<div class="keywords">${kw}</div>` : ""}
    ${details}
    ${changeNote}
    <div class="actions">${actions.join("")}</div>`;

  el.querySelectorAll(".tag[data-cat]").forEach((t) => {
    t.onclick = () => { state.category = t.dataset.cat; buildCategories(); render(); window.scrollTo({ top: 0, behavior: "smooth" }); };
  });

  // Lazy-load articles/excerpts from the per-doc file when the disclosure opens.
  const det = el.querySelector("details.more");
  if (det) det.addEventListener("toggle", async () => {
    if (!det.open || det.dataset.loaded) return;
    det.dataset.loaded = "1";
    const body = det.querySelector(".more-body");
    const full = await loadDoc(r);
    const arts = (full && full.articles) || [], exc = (full && full.excerpts) || [];
    body.innerHTML =
      (arts.length ? `<ul class="arts">${arts.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "") +
      (exc.length ? `<ul class="excs">${exc.map((x) => `<li class="excerpt">«${esc(x)}»</li>`).join("")}</ul>` : "") ||
      `<span class="muted">(χωρίς επιπλέον στοιχεία)</span>`;
  });
  return el;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const trim = (s, n) => (s.length > n ? s.slice(0, n).trim() + "…" : s);

// Minimal, dependency-free markdown → HTML for the AI answer (GFM tables, bold,
// lists, headings, [n] citations). Input is escaped first, so it is XSS-safe.
function mdInline(s) {
  return s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
          .replace(/\[(\d+)\]/g, '<sup class="cite">[$1]</sup>');
}
function mdToHtml(md) {
  const L = esc(md).split(/\r?\n/);
  let html = "", i = 0;
  const isRow = (s) => /^\s*\|.*\|\s*$/.test(s);
  const cells = (s) => s.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  while (i < L.length) {
    const line = L[i];
    if (isRow(line) && i + 1 < L.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(L[i + 1])) {
      const head = cells(line); i += 2; const rows = [];
      while (i < L.length && isRow(L[i])) { rows.push(cells(L[i])); i++; }
      html += '<table class="md"><thead><tr>' + head.map((h) => `<th>${mdInline(h)}</th>`).join("") +
        "</tr></thead><tbody>" + rows.map((r) => "<tr>" + r.map((c) => `<td>${mdInline(c)}</td>`).join("") + "</tr>").join("") +
        "</tbody></table>"; continue;
    }
    let m;
    if ((m = line.match(/^#{1,4}\s+(.*)/))) { html += `<h4>${mdInline(m[1])}</h4>`; i++; continue; }
    if (/^\s*[-•*]\s+/.test(line)) {
      const it = []; while (i < L.length && /^\s*[-•*]\s+/.test(L[i])) { it.push(L[i].replace(/^\s*[-•*]\s+/, "")); i++; }
      html += "<ul>" + it.map((x) => `<li>${mdInline(x)}</li>`).join("") + "</ul>"; continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const it = []; while (i < L.length && /^\s*\d+[.)]\s+/.test(L[i])) { it.push(L[i].replace(/^\s*\d+[.)]\s+/, "")); i++; }
      html += "<ol>" + it.map((x) => `<li>${mdInline(x)}</li>`).join("") + "</ol>"; continue;
    }
    if (line.trim() === "") { i++; continue; }
    const para = [line]; i++;
    while (i < L.length && L[i].trim() !== "" && !isRow(L[i]) &&
           !/^\s*[-•*]\s+/.test(L[i]) && !/^\s*\d+[.)]\s+/.test(L[i]) && !/^#{1,4}\s+/.test(L[i])) {
      para.push(L[i]); i++;
    }
    html += `<p>${mdInline(para.join(" "))}</p>`;
  }
  return html;
}

boot();
