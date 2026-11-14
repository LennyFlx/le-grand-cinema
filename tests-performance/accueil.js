
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 5,
  duration: "15s",
};

export default function () {
  const response = http.get("http://127.0.0.1:8000/");

  check(response, {
    "Accueil accessible (HTTP 200)": (r) => r.status === 200,
  });

  sleep(1);
}
