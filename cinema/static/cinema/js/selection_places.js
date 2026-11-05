(() => {
    const page = document.querySelector('[data-selection-places]');
    if (!page) return;

    const sieges = Array.from(page.querySelectorAll('[data-place]'));
    const prixCentimes = Math.round(Number(page.dataset.prix) * 100);
    const montant = new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'EUR'});
    const selection = new Set(sieges.filter(siege => siege.dataset.selectionnee === 'true'));
    const erreur = page.querySelector('[data-erreur]');
    const liste = page.querySelector('[data-places]');

    function actualiser() {
        const places = sieges.filter(siege => selection.has(siege));
        const champs = page.querySelector('[data-champs-selection]');
        champs.replaceChildren();
        for (const siege of sieges) {
            const choisie = selection.has(siege);
            siege.setAttribute('aria-pressed', String(choisie));
            siege.setAttribute('aria-label', `Place ${siege.dataset.place} : ${choisie ? 'Sélectionnée' : 'Disponible'}`);
            siege.querySelector('[data-symbole]').textContent = choisie ? '✓' : '+';
        }
        for (const siege of places) {
            const champ = document.createElement('input');
            champ.type = 'hidden';
            champ.name = 'places';
            champ.value = siege.dataset.placeId;
            champs.append(champ);
        }
        page.querySelector('[data-continuer]').disabled = places.length === 0;
        liste.replaceChildren();
        if (!places.length) liste.textContent = 'Aucune place sélectionnée';
        for (const siege of places) {
            const retirer = document.createElement('button');
            retirer.type = 'button';
            retirer.className = 'selection-etiquette';
            retirer.textContent = `${siege.dataset.place} ×`;
            retirer.setAttribute('aria-label', `Retirer la place ${siege.dataset.place}`);
            retirer.addEventListener('click', () => {
                basculer(siege);
                siege.focus({preventScroll: true});
            });
            liste.append(retirer);
        }
        page.querySelector('[data-compteur]').textContent = `${places.length} place${places.length > 1 ? 's' : ''}`;
        page.querySelector('[data-total]').textContent = montant.format(places.length * prixCentimes / 100);
    }

    function basculer(siege) {
        if (!selection.has(siege) && selection.size >= 8) {
            erreur.textContent = 'Vous pouvez sélectionner jusqu’à 8 places par réservation.';
            return;
        }
        if (selection.has(siege)) selection.delete(siege);
        else selection.add(siege);

        erreur.textContent = '';
        actualiser();
    }

    for (const siege of sieges) {
        siege.disabled = false;
        siege.addEventListener('click', () => basculer(siege));
    }
    page.querySelector('[data-resume]').hidden = false;
    actualiser();
    page.querySelector('[data-erreurs-serveur]')?.focus();
})();
