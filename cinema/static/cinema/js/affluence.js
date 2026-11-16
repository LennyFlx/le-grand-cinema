/* Les séries sont sérialisées par json_script ; aucune insertion HTML. */
(() => {
  "use strict";
  const source = document.getElementById("affluence-donnees");
  if (!source) return;
  const series = JSON.parse(source.textContent);
  document.querySelectorAll("canvas[data-affluence]").forEach((canvas) => {
    const identifiant = canvas.dataset.affluence;
    const erreur = document.querySelector('[data-erreur-graphique="' + identifiant + '"]');
    try {
      const points = series[identifiant];
      const couleur = getComputedStyle(document.documentElement).getPropertyValue("--body-fg").trim() || "#333";
      new Chart(canvas, {
        type: "line",
        data: {
          labels: points.map((point) => point.jour.split("-").reverse().join("/")),
          datasets: [{
            label: "Places réservées",
            data: points.map((point) => point.places_reservees),
            borderColor: "#3095c2",
            backgroundColor: "#3095c2",
            borderWidth: 2,
            pointRadius: points.length > 60 ? 0 : 3,
            tension: 0,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: couleur, maxTicksLimit: 12 }, title: { display: true, text: "Date de séance", color: couleur } },
            y: { beginAtZero: true, ticks: { color: couleur, precision: 0 }, title: { display: true, text: "Places réservées", color: couleur } },
          },
        },
      });
    } catch {
      canvas.hidden = true;
      if (erreur) erreur.hidden = false;
    }
  });
})();
