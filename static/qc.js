const grid = document.getElementById("grid");
const errorBox = document.getElementById("error");
let filter = "all";
let jobs = [];
const rendered = new Map(); // job id -> signature of what's on screen (so playing videos aren't reset)

const ACTIVE = ["queued", "processing", "stitching"];
const LABEL = {
  queued: "Queued", processing: "Processing", stitching: "Stitching", ready_for_qc: "Ready for QC",
  approved: "Approved", rejected: "Rejected", failed: "Failed",
};

function matches(job) {
  if (filter === "all") return true;
  if (filter === "processing") return ACTIVE.includes(job.status);
  return job.status === filter;
}

function el(tag, attrs = {}, text) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}

function ago(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 90 ? `${Math.round(s)}s ago` : `${Math.round(s / 60)} min ago`;
}

function card(job) {
  const c = el("article", { class: "card", "data-id": job.id });
  const head = el("div", { class: "card-head" });
  head.append(el("span", { class: "num" }, `#${job.ticket}`), el("span", { class: "who" }, job.name),
    el("span", { class: `badge ${job.status}` }, LABEL[job.status] || job.status));
  c.append(head);
  c.append(el("div", { class: "muted" }, `${job.template_id} · ${job.phone} · ${ago(job.created_at)}`));

  const clips = el("div", { class: "clips" });
  for (const [name, clip] of Object.entries(job.clips)) {
    const tries = clip.attempts > 1 ? ` (try ${clip.attempts})` : "";
    clips.append(el("span", { class: clip.status, title: clip.error || clip.status }, `${name}: ${clip.status}${tries}`));
  }
  c.append(clips);
  if (job.error) c.append(el("div", { class: "err" }, job.error));

  const media = el("div", { class: "media-row" });
  media.append(el("img", { src: job.photo_url, alt: "Visitor photo", loading: "lazy" }));
  if (job.video_url && !ACTIVE.includes(job.status)) {
    media.append(el("video", { src: job.video_url, controls: "", playsinline: "", preload: "metadata" }));
  }
  c.append(media);

  const actions = el("div", { class: "actions" });
  const add = (label, cls, fn) => {
    const b = el("button", { class: cls }, label);
    b.addEventListener("click", () => act(b, fn));
    actions.append(b);
  };
  if (job.status === "ready_for_qc") add("Approve", "approve", () => approve(job));
  if (["ready_for_qc", "failed"].includes(job.status) && !job.running) {
    for (const clip of ["A", "B", "C"]) add(`Rerun ${clip}`, "", () => post(`/jobs/${job.id}/rerun/${clip}`));
  }
  if (job.status === "approved") add("Show QR", "approve", () => showQr(job));
  if (job.status !== "rejected") {
    add("Reject", "reject", () => confirm(`Reject #${job.ticket}? ${job.status === "approved" ? "The visitor's link will stop working." : ""}`)
      && post(`/jobs/${job.id}/reject`));
  }
  if (actions.children.length) c.append(actions);
  return c;
}

async function act(button, fn) {
  button.disabled = true;
  errorBox.textContent = "";
  try { await fn(); } catch (ex) { errorBox.textContent = ex.message; }
  button.disabled = false;
  refresh();
}

async function post(url) {
  const res = await fetch(url, { method: "POST" });
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) location.href = "/login?next=/qc";
  if (!res.ok) throw new Error(body.detail || `Server error (${res.status})`);
  return body;
}

async function approve(job) {
  const body = await post(`/jobs/${job.id}/approve`);
  openModal(job, body.result_url, body.qr_png_url);
}

function showQr(job) { openModal(job, job.result_url, job.qr_png_url); }

function openModal(job, url, qr) {
  document.getElementById("modal-title").textContent = `#${job.ticket} · ${job.name}`;
  document.getElementById("modal-qr").src = qr;
  document.getElementById("modal-url").textContent = url;
  document.getElementById("modal").classList.remove("hidden");
}
document.getElementById("modal-close").addEventListener("click", () => document.getElementById("modal").classList.add("hidden"));

function render() {
  const visible = jobs.filter(matches);
  const keep = new Set(visible.map((j) => String(j.id)));
  for (const node of [...grid.children]) {
    if (!keep.has(node.dataset.id)) { node.remove(); rendered.delete(node.dataset.id); }
  }
  visible.forEach((job, i) => {
    const id = String(job.id);
    const sig = `${job.updated_at}|${job.running}|${job.status}`;
    let node = grid.querySelector(`[data-id="${id}"]`);
    if (!node || rendered.get(id) !== sig) {
      const fresh = card(job);
      if (node) node.replaceWith(fresh); else grid.append(fresh);
      node = fresh;
      rendered.set(id, sig);
    }
    if (grid.children[i] !== node) grid.insertBefore(node, grid.children[i]);
  });
  document.getElementById("empty").classList.toggle("hidden", visible.length > 0);
}

async function refresh() {
  try {
    const res = await fetch("/jobs");
    if (res.status === 401) { location.href = "/login?next=/qc"; return; }
    if (!res.ok) throw new Error(`Server error (${res.status})`);
    jobs = (await res.json()).jobs;
    render();
  } catch (ex) {
    errorBox.textContent = `Can't load jobs: ${ex.message}`;
  }
}

document.getElementById("filters").addEventListener("click", (e) => {
  if (!e.target.dataset.f) return;
  filter = e.target.dataset.f;
  for (const b of document.querySelectorAll("#filters button")) b.classList.toggle("on", b === e.target);
  render();
});

refresh();
setInterval(refresh, 5000);
