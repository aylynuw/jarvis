// Dashboard core: mounts the shared 3D hologram behind the period ring.
// If WebGL is missing, nothing happens and the flat SVG core stays in place.
import { mountCore } from "./jarvis-core.js";

const canvas = document.getElementById("core-3d");
const core = mountCore(canvas, { getState: () => document.body.dataset.state, background: "--color-paper" });
if (core) {
  document.querySelector(".core-stage").classList.add("has-3d");
  // With motion reduced the scene is drawn once per state change.
  new MutationObserver(() => { if (matchMedia("(prefers-reduced-motion: reduce)").matches) core.redraw(); })
    .observe(document.body, { attributes: true, attributeFilter: ["data-state"] });
}
