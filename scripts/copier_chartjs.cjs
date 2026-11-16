/* Version navigateur minifiée ; les licences MIT sont conservées. */
const { copyFileSync, mkdirSync, readFileSync, writeFileSync } = require("node:fs");
const { join } = require("node:path");
const racine = join(__dirname, "..");
const destination = join(racine, "cinema", "static", "cinema", "vendor", "chartjs");
mkdirSync(destination, { recursive: true });
const graphique = readFileSync(join(racine, "node_modules/chart.js/dist/chart.umd.min.js"), "utf8");
writeFileSync(
  join(destination, "chart.umd.js"),
  graphique.replace("//# sourceMappingURL=chart.umd.min.js.map", ""),
);
for (const [source, cible] of [
  ["chart.js/LICENSE.md", "LICENSE-Chart.js.md"],
  ["@kurkle/color/LICENSE.md", "LICENSE-color.md"],
]) {
  copyFileSync(join(racine, "node_modules", source), join(destination, cible));
}
console.log("Chart.js et ses licences copiés dans les fichiers statiques Django.");
