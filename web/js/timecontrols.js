// timecontrols.js — pause/resume and the 1×..100× speed multipliers.
// Sends control messages up the WebSocket; reflects authoritative speed coming
// back in the overview payload so two phones stay in sync.

import { send, store } from "./ws.js";

let lastSpeed = 1;

export function initTimeControls() {
  const pauseBtn = document.getElementById("btn-pause");
  const speedBtns = [...document.querySelectorAll(".time-btn.speed")];

  pauseBtn.addEventListener("click", () => {
    const paused = pauseBtn.classList.toggle("paused");
    send({ action: paused ? "pause" : "speed", speed: paused ? 0 : lastSpeed });
  });

  speedBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      lastSpeed = Number(btn.dataset.speed);
      pauseBtn.classList.remove("paused");
      send({ action: "speed", speed: lastSpeed });
    });
  });

  // reflect server-authoritative speed
  store.on("overview", ({ speed, paused }) => {
    pauseBtn.classList.toggle("paused", paused);
    if (speed > 0) lastSpeed = speed;
    speedBtns.forEach((b) =>
      b.classList.toggle("active", !paused && Number(b.dataset.speed) === speed));
  });
}
