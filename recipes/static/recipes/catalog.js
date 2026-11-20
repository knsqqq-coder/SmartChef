(() => {
  const dataNode = document.getElementById("product-catalog-data");
  const form = document.getElementById("recipe-form");
  if (!dataNode || !form) return;

  const catalog = JSON.parse(document.getElementById("product-catalog-data").textContent);
  const language = catalog.language === "ru" ? "ru" : "kk";
  const cards = [...document.querySelectorAll(".product-card[data-product-id][data-category]")];
  const search = document.getElementById("catalog-search");
  const selectedBox = document.getElementById("catalog-selected");
  const count = document.getElementById("catalog-count");
  const clearButton = document.getElementById("catalog-clear");
  const emptySelection = document.getElementById("selection-empty");
  const noResults = document.getElementById("catalog-empty");
  const categoryButtons = [...document.querySelectorAll(".filter-button[data-category]")];
  const byId = new Map(catalog.products.map((product) => [product.id, product]));
  let activeCategory = "all";

  function selectedInputs() {
    return cards
      .map((card) => card.querySelector('input[type="checkbox"][name="selected_products"]'))
      .filter((input) => input && input.checked);
  }

  function renderSelection() {
    const inputs = selectedInputs();
    count.textContent = String(inputs.length);
    selectedBox.replaceChildren();
    emptySelection.hidden = inputs.length > 0;

    for (const input of inputs) {
      const product = byId.get(input.value);
      if (!product) continue;

      const tag = document.createElement("span");
      tag.className = "selected-tag";
      const name = document.createElement("span");
      name.textContent = product[language];
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove-tag";
      remove.dataset.productId = product.id;
      remove.textContent = "×";
      remove.setAttribute(
        "aria-label",
        language === "ru" ? "Убрать " + product.ru : product.kk + " өнімін алып тастау",
      );
      remove.addEventListener("click", () => {
        const checkbox = cards
          .map((card) => card.querySelector('input[type="checkbox"][name="selected_products"]'))
          .find((candidate) => candidate && candidate.value === remove.dataset.productId);
        if (checkbox) checkbox.checked = false;
        renderSelection();
      });
      tag.append(name, remove);
      selectedBox.append(tag);
    }
  }

  function applyFilters() {
    const query = search.value.trim().toLocaleLowerCase(language);
    let visibleCount = 0;

    for (const card of cards) {
      const product = byId.get(card.dataset.productId);
      if (!product) {
        card.hidden = true;
        continue;
      }
      const searchable = (product.kk + " " + product.ru).trim().toLocaleLowerCase(language);
      const matchesText = !query || searchable.includes(query);
      const matchesCategory = activeCategory === "all" || product.category === activeCategory;
      card.hidden = !(matchesText && matchesCategory);
      if (!card.hidden) visibleCount += 1;
    }

    noResults.hidden = visibleCount !== 0;
  }

  categoryButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activeCategory = button.dataset.category;
      categoryButtons.forEach((candidate) => {
        const active = candidate === button;
        candidate.classList.toggle("active", active);
        candidate.setAttribute("aria-pressed", String(active));
      });
      applyFilters();
    });
  });

  search.addEventListener("input", applyFilters);
  cards.forEach((card) => {
    const checkbox = card.querySelector('input[type="checkbox"][name="selected_products"]');
    checkbox.addEventListener("change", renderSelection);
  });

  clearButton.addEventListener("click", () => {
    cards.forEach((card) => {
      const checkbox = card.querySelector('input[type="checkbox"][name="selected_products"]');
      checkbox.checked = false;
    });
    renderSelection();
  });

  renderSelection();
  applyFilters();
})();
