
import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = "http://127.0.0.1:8000";

// URL fournies au lancement du test
const catalogue = __ENV.CATALOGUE;
const film = __ENV.FILM;
const seance = __ENV.SEANCE;

if (!catalogue || !film || !seance) {
  throw new Error("Les trois URL du parcours sont obligatoires.");
}

export const options = {
  stages: [
    { duration: "10s", target: 5 },
    { duration: "10s", target: 10 },
    { duration: "10s", target: 10 },
    { duration: "10s", target: 0 },
  ],


thresholds: {
  http_req_duration: ["p(95)<500"],
  http_req_failed: ["rate<0.01"],
  checks: ["rate>0.99"],

  // Temps de réponse par page
  "http_req_duration{name:catalogue}": ["p(95)<500"],
  "http_req_duration{name:detail_film}": ["p(95)<500"],
  "http_req_duration{name:detail_seance}": ["p(95)<500"],
},

};

export default function () {
  // 1. Catalogue
  const accueil = http.get(`${BASE_URL}${catalogue}`, {
    tags: { name: "catalogue" },
  });

  check(accueil, {
    "Catalogue accessible": (r) => r.status === 200,
  });

  sleep(1);

  // 2. Fiche film
  const fiche = http.get(`${BASE_URL}${film}`, {
    tags: { name: "detail_film" },
  });

  check(fiche, {
    "Fiche film accessible": (r) => r.status === 200,
  });

  sleep(1);

  // 3. Détail séance
  const detail = http.get(`${BASE_URL}${seance}`, {
    tags: { name: "detail_seance" },
  });

  check(detail, {
    "Détail séance accessible": (r) => r.status === 200,
  });

  sleep(1);
}
