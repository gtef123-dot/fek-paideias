// Ρυθμίσεις frontend.
// SYNTH_ENDPOINT: το URL του Cloudflare Worker που κρατά ΚΡΥΦΑ το κλειδί σου και
// κάνει τη σύνθεση (Επίπεδο 2). Έτσι κανένας δάσκαλος δεν βάζει κλειδί και το
// δικό σου ΔΕΝ εκτίθεται στη σελίδα. Βάλ' το μετά το deploy του Worker, π.χ.:
//   SYNTH_ENDPOINT: "https://fek-paideias-synth.<account>.workers.dev"
// Άδειο = η AI-σύνθεση είναι ανενεργή (τοπικά: localStorage.fek_dev_key για δοκιμή).
window.FEK_CONFIG = {
  SYNTH_ENDPOINT: ""
};
