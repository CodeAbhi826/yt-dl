function showToast(message, type="success") {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = "toast show " + type;
  setTimeout(() => toast.classList.remove("show"), 3000);
}
