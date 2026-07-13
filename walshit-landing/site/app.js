const dialog = document.querySelector("#help-dialog");
const closeButton = dialog.querySelector(".close");

document.querySelectorAll(".help-trigger").forEach((button) => {
  button.addEventListener("click", () => dialog.showModal());
});

closeButton.addEventListener("click", () => dialog.close());

dialog.addEventListener("click", (event) => {
  if (event.target === dialog) {
    dialog.close();
  }
});
