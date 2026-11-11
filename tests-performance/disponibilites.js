
import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = "http://127.0.0.1:8000";
const SEANCE = __ENV.SEANCE;

if (!SEANCE) {
  throw new Error("L'URL de la séance est obligatoire.");
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
  },
};

export default function () {
  const response = http.get(`${BASE_URL}${SEANCE}`, {
    tags: { name: "disponibilites" },
  });

  check(response, {
    "Plan de salle accessible": (r) => r.status === 200,
  });

  sleep(1);
}
