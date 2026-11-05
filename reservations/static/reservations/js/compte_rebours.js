(() => {
    const compteur = document.querySelector('[data-compte-rebours]');
    if (!compteur) return;
    // Durée fournie par le serveur : l'heure locale du navigateur n'est pas utilisée.
    const echeance = performance.now() + Number(compteur.dataset.secondes) * 1000;
    let termine = false;
    function actualiser() {
        if (termine) return;
        const secondes = Math.max(0, Math.ceil((echeance - performance.now()) / 1000));
        compteur.textContent = `${Math.floor(secondes / 60)}:${String(secondes % 60).padStart(2, '0')}`;
        if (secondes === 0) {
            termine = true;
            window.location.reload();
        }
    }
    actualiser();
    const intervalle = window.setInterval(() => {
        actualiser();
        if (termine) window.clearInterval(intervalle);
    }, 1000);
})();
