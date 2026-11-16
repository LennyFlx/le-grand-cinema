const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");

const code = readFileSync(join(__dirname, "../../cinema/static/cinema/js/affluence.js"), "utf8");

function executer({ series, disponible = true } = {}) {
  const graphiques = [];
  const erreurs = {};
  const canvas = Object.keys(series || {}).map((identifiant) => {
    erreurs[identifiant] = { hidden: true };
    return { dataset: { affluence: identifiant }, hidden: false };
  });
  const contexte = {
    document: {
      documentElement: {},
      getElementById: () => series ? { textContent: JSON.stringify(series) } : null,
      querySelectorAll: () => canvas,
      querySelector: (selecteur) => erreurs[selecteur.split('"')[1]],
    },
    getComputedStyle: () => ({ getPropertyValue: () => "#eee" }),
  };
  if (disponible) {
    contexte.Chart = function (element, configuration) {
      graphiques.push({ element, configuration });
    };
  }
  runInNewContext(code, contexte);
  return { graphiques, erreurs, canvas };
}

test("chaque courbe conserve ses dates locales et ses jours à zéro", () => {
  const resultat = executer({ series: {
    principale: [
      { jour: "2027-03-27", places_reservees: 2 },
      { jour: "2027-03-28", places_reservees: 0 },
    ],
    reference: [{ jour: "2026-03-27", places_reservees: 3 }],
  } });
  assert.equal(resultat.graphiques.length, 2);
  const principale = resultat.graphiques[0].configuration;
  assert.deepEqual(Array.from(principale.data.labels), ["27/03/2027", "28/03/2027"]);
  assert.deepEqual(Array.from(principale.data.datasets[0].data), [2, 0]);
  assert.equal(principale.options.scales.y.beginAtZero, true);
  assert.equal(principale.options.scales.y.ticks.precision, 0);
  assert.equal(principale.options.animation, false);
  assert.deepEqual(
    Array.from(resultat.graphiques[1].configuration.data.labels), ["27/03/2026"],
  );
});

test("si Chart.js manque, le message de repli remplace le graphique", () => {
  const resultat = executer({
    series: { principale: [{ jour: "2027-03-27", places_reservees: 2 }] },
    disponible: false,
  });
  assert.equal(resultat.canvas[0].hidden, true);
  assert.equal(resultat.erreurs.principale.hidden, false);
  assert.equal(resultat.graphiques.length, 0);
});

test("une page sans données graphiques ne déclenche aucun graphique", () => {
  assert.equal(executer().graphiques.length, 0);
});

test("la version locale de Chart.js contient les composants nécessaires", () => {
  const Chart = require("../../cinema/static/cinema/vendor/chartjs/chart.umd.js");
  assert.equal(Chart.version, "4.5.1");
  assert.ok(Chart.registry.getController("line"));
  assert.ok(Chart.registry.getScale("category"));
  assert.ok(Chart.registry.getScale("linear"));
});
