// toast.js — the transient event banner ("a meteor struck!").
let timer = null;
const el = () => document.getElementById("event-toast");

export function showToast(text, ms = 3200) {
  const t = el();
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(timer);
  timer = setTimeout(() => t.classList.remove("show"), ms);
}
