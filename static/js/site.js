// Small, dependency-free interactions for the public pages.
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const fine = matchMedia("(pointer: fine)").matches;

// Split .reveal text into letters so they rise one by one (screen readers get the original text).
document.querySelectorAll(".reveal").forEach((el) => {
  const text = el.textContent.trim();
  el.setAttribute("aria-label", text);
  el.textContent = "";
  let i = 0;
  text.split(/\s+/).forEach((word, w) => {
    if (w) { const sp = document.createElement("span"); sp.className = "sp"; sp.setAttribute("aria-hidden", "true"); el.appendChild(sp); }
    const wrap = document.createElement("span");          // a word never breaks across lines
    wrap.className = "word";
    wrap.setAttribute("aria-hidden", "true");
    for (const c of word) {
      const s = document.createElement("span");
      s.className = "ch";
      s.style.setProperty("--i", i++);
      s.textContent = c;
      wrap.appendChild(s);
    }
    el.appendChild(wrap);
  });
});

// Magnetic buttons (desktop pointer only).
if (fine && !reduce) {
  document.querySelectorAll(".btn").forEach((b) => {
    b.addEventListener("pointermove", (e) => {
      const r = b.getBoundingClientRect();
      b.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * 0.25}px, ${(e.clientY - r.top - r.height / 2) * 0.35}px)`;
    });
    b.addEventListener("pointerleave", () => { b.style.transform = ""; });
  });
}

// The video frame tilts toward the pointer.
const frame = document.querySelector(".frame");
if (frame && fine && !reduce) {
  frame.addEventListener("pointermove", (e) => {
    const r = frame.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    frame.style.transform = `perspective(900px) rotateY(${x * 8}deg) rotateX(${-y * 8}deg)`;
  });
  frame.addEventListener("pointerleave", () => { frame.style.transform = ""; });
}

// Video: big play button, dim the 3D scene while it plays, confetti when it ends.
const video = document.querySelector(".frame video");
if (video) {
  const play = document.querySelector(".play");
  play?.addEventListener("click", () => { video.play(); });
  video.addEventListener("play", () => { frame.classList.add("is-playing"); document.body.classList.add("playing"); video.controls = true; });
  video.addEventListener("pause", () => document.body.classList.remove("playing"));
  video.addEventListener("ended", () => { document.body.classList.remove("playing"); window.awardScene?.celebrate(); });
}

// Share: Web Share API, falling back to copying the link.
const share = document.getElementById("share");
if (share) {
  share.addEventListener("click", async () => {
    const note = document.getElementById("note");
    const url = share.dataset.url;
    if (navigator.share) {
      try { await navigator.share({ title: share.dataset.title, url }); return; }
      catch (e) { if (e.name === "AbortError") return; }
    }
    try { await navigator.clipboard.writeText(url); note.textContent = "Link copied. Paste it anywhere to share."; }
    catch (_) { note.textContent = url; }
  });
}
document.getElementById("download")?.addEventListener("click", () => window.awardScene?.celebrate());
