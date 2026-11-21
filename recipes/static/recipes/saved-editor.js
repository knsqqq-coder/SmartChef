(() => {
  document.querySelectorAll("[data-recipe-editor]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }

      const button = form.querySelector("[data-editor-submit]");
      const waiting = form.querySelector("[data-editor-wait]");
      if (!button) return;

      form.dataset.submitting = "true";
      button.disabled = true;
      button.textContent = button.dataset.waitLabel || button.textContent;
      if (waiting) waiting.hidden = false;
    });
  });
})();
