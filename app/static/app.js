const fileInput = document.getElementById("fileInput");
const uploadBtn = document.getElementById("uploadBtn");
const resetBtn = document.getElementById("resetBtn");
const uploadStatus = document.getElementById("uploadStatus");
const docList = document.getElementById("docList");
const questionInput = document.getElementById("question");
const modeSelect = document.getElementById("mode");
const askBtn = document.getElementById("askBtn");
const benchmarkBtn = document.getElementById("benchmarkBtn");
const singleResult = document.getElementById("singleResult");
const compareResult = document.getElementById("compareResult");

async function refreshDocuments() {
  const res = await fetch("/api/documents");
  const docs = await res.json();

  docList.innerHTML = "";
  docs.forEach((doc) => {
    const li = document.createElement("li");
    li.textContent = `${doc.doc_name} | pages=${doc.pages}, chunks=${doc.chunks}`;
    docList.appendChild(li);
  });
}

uploadBtn.addEventListener("click", async () => {
  const files = fileInput.files;
  if (!files.length) {
    uploadStatus.textContent = "Select one or more files first.";
    return;
  }

  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }

  uploadStatus.textContent = "Uploading...";
  const res = await fetch("/api/documents/upload", { method: "POST", body: form });
  if (!res.ok) {
    uploadStatus.textContent = `Upload failed: ${res.status}`;
    return;
  }

  uploadStatus.textContent = "Upload completed.";
  await refreshDocuments();
});

resetBtn.addEventListener("click", async () => {
  const ok = window.confirm("Reset current corpus? This clears loaded documents before your next upload.");
  if (!ok) {
    return;
  }

  uploadStatus.textContent = "Resetting corpus...";
  const res = await fetch("/api/documents/reset?delete_uploaded_files=true", {
    method: "POST",
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    uploadStatus.textContent = data.detail || `Reset failed: ${res.status}`;
    return;
  }

  const data = await res.json();
  uploadStatus.textContent = `Corpus reset: removed ${data.removed_documents} document(s).`;
  singleResult.textContent = "";
  compareResult.textContent = "";
  await refreshDocuments();
});

askBtn.addEventListener("click", async () => {
  const question = questionInput.value.trim();
  if (!question) {
    singleResult.textContent = "Enter a question first.";
    return;
  }

  singleResult.textContent = "Running...";
  const payload = { question, mode: modeSelect.value, top_k: 8 };
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) {
    singleResult.textContent = data.detail || "Request failed.";
    return;
  }

  singleResult.textContent = formatResponse(data);
});

benchmarkBtn.addEventListener("click", async () => {
  const question = questionInput.value.trim();
  if (!question) {
    compareResult.textContent = "Enter a question first.";
    return;
  }

  compareResult.textContent = "Running benchmark...";
  const res = await fetch("/api/benchmark", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, top_k: 8 }),
  });

  const data = await res.json();
  if (!res.ok) {
    compareResult.textContent = data.detail || "Benchmark failed.";
    return;
  }

  compareResult.textContent = [
    "RAG",
    "-----",
    formatResponse(data.rag),
    "\nLONG CONTEXT",
    "-----------",
    formatResponse(data.long_context),
  ].join("\n");
});

function formatResponse(data) {
  const cites = (data.citations || [])
    .map((c) => {
      const para = (c.paragraph_start && c.paragraph_end)
        ? ` ¶${c.paragraph_start}-${c.paragraph_end}`
        : "";
      return `- ${c.doc_name} p.${c.page}${para}: ${c.snippet}`;
    })
    .join("\n");

  return [
    `Mode: ${data.mode}`,
    `Latency: ${data.latency_ms} ms`,
    `Context: chunks=${data.context_chunks}, chars=${data.context_chars}, tokens=${data.context_tokens || 0}`,
    "",
    "Answer:",
    data.answer,
    "",
    "Citations:",
    cites || "(none)",
  ].join("\n");
}

refreshDocuments();
