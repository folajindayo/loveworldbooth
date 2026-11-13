const $ = (id) => document.getElementById(id);
const form = $("form");
let pollTimer = null;

const STATUS_TEXT = {
  queued: ["In queue", "Starts as soon as a slot is free."],
  processing: ["Creating the award scenes", "Usually 5–8 minutes."],
  stitching: ["Finishing the video", "Less than a minute."],
  ready_for_qc: ["Ready for review", "Approve it on the QC page."],
  approved: ["Approved", "Show the visitor their QR code on the QC page."],
  failed: ["Needs attention", "Open the QC page to rerun or reject."],
  rejected: ["Rejected", ""],
};

function renderTemplates() {
  const box = $("templates");
  window.TEMPLATES.forEach((t, i) => {
    const id = `tpl-${t.id}`;
    box.insertAdjacentHTML("beforeend",
      `<input type="radio" name="template_id" id="${id}" value="${t.id}" ${i === 0 ? "checked" : ""}>` +
      `<label for="${id}"></label>`);
    box.querySelector(`label[for="${id}"]`).textContent = t.label;
  });
}

$("photo").addEventListener("change", () => {
  const file = $("photo").files[0];
  const img = $("preview");
  if (!file) { img.style.display = "none"; return; }
  img.src = URL.createObjectURL(file);
  img.style.display = "block";
  $("photo-label").textContent = "Retake photo";
  $("error").textContent = "";
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("error");
  err.textContent = "";
  if (!$("name").value.trim()) return (err.textContent = "Enter the visitor's name.");
  if (!$("phone").value.trim()) return (err.textContent = "Enter the visitor's phone number.");
  if (!$("photo").files[0]) return (err.textContent = "Take the visitor's photo.");
  if (!$("consent").checked) return (err.textContent = "The visitor must agree to the consent statement.");

  const btn = $("submit");
  btn.disabled = true;
  btn.textContent = "Checking photo…";
  try {
    const res = await fetch("/jobs", { method: "POST", body: new FormData(form) });
    const body = await res.json().catch(() => ({}));
    if (res.status === 401) { location.href = "/login?next=/capture"; return; }
    if (!res.ok) throw new Error(body.detail || `Server error (${res.status})`);
    showTicket(body.job_id, body.ticket);
  } catch (ex) {
    err.textContent = ex.message === "Failed to fetch" ? "No connection to the server. Check Wi-Fi and try again." : ex.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Create video";
  }
});

function showTicket(jobId, ticket) {
  $("ticket").textContent = ticket;
  $("visitor").textContent = $("name").value.trim();
  form.classList.add("hidden");
  $("done").classList.remove("hidden");
  window.scrollTo(0, 0);
  poll(jobId);
}

async function poll(jobId) {
  clearTimeout(pollTimer);
  try {
    const res = await fetch(`/jobs/${jobId}`);
    if (res.ok) {
      const job = await res.json();
      const [title, detail] = STATUS_TEXT[job.status] || [job.status, ""];
      $("status").textContent = title;
      $("status-detail").textContent = job.status === "failed" && job.error ? job.error : detail;
      if (["approved", "rejected"].includes(job.status)) return;
    }
  } catch (_) { /* transient network error: keep polling */ }
  pollTimer = setTimeout(() => poll(jobId), 4000);
}

$("next").addEventListener("click", () => {
  clearTimeout(pollTimer);
  form.reset();
  form.querySelector('input[name="template_id"]').checked = true;
  $("preview").style.display = "none";
  $("photo-label").textContent = "Take photo";
  $("done").classList.add("hidden");
  form.classList.remove("hidden");
  $("name").focus();
});

renderTemplates();
