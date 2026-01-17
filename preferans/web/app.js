document.addEventListener('DOMContentLoaded', async () => {
    const statusEl = document.getElementById('status');
    const cardsEl = document.getElementById('cards');

    try {
        const response = await fetch('/api/health');
        const data = await response.json();
        statusEl.textContent = `Server status: ${data.status}`;

        // Load and display cards (uses default style)
        const cardsResponse = await fetch('/api/cards');
        const cards = await cardsResponse.json();

        cards.forEach(card => {
            const img = document.createElement('img');
            img.src = `/api/cards/${card.card_id}/image`;
            img.alt = `${card.rank} of ${card.suit}`;
            img.className = 'card';
            img.title = `${card.rank} of ${card.suit}`;
            cardsEl.appendChild(img);
        });

        // Add card back at the end
        const backImg = document.createElement('img');
        backImg.src = '/api/styles/classic/back';
        backImg.alt = 'Card back';
        backImg.className = 'card';
        backImg.title = 'Card back';
        cardsEl.appendChild(backImg);
    } catch (error) {
        statusEl.textContent = 'Server connection failed';
    }
});
