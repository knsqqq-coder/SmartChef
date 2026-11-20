(() => {
  const form = document.getElementById("cooking-actions");
  if (!form) return;

  let confirmSubmitted = false;
  form.addEventListener("submit", (event) => {
    if (event.submitter?.dataset.confirmStep !== "true") return;
    if (confirmSubmitted) {
      event.preventDefault();
      return;
    }
    confirmSubmitted = true;
    event.submitter.setAttribute("aria-disabled", "true");
  });
})();
