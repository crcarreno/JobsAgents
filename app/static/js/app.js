document.addEventListener("submit", function (event) {
  var form = event.target;
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
    event.preventDefault();
  }
});

document.addEventListener("click", function (event) {
  var toggle = event.target.closest("[data-toggle-target]");
  if (!toggle) return;
  var target = document.querySelector(toggle.getAttribute("data-toggle-target"));
  if (!target) return;
  var isHidden = target.hasAttribute("hidden");
  if (isHidden) {
    target.removeAttribute("hidden");
  } else {
    target.setAttribute("hidden", "");
  }
  toggle.textContent = isHidden ? "Hide details" : "Show details";
});
