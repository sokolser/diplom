const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');

function makeUrl(path) {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

function getFilenameFromDisposition(disposition) {
  if (!disposition) return null;
  const utfMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utfMatch?.[1]) return decodeURIComponent(utfMatch[1]);
  const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
  return plainMatch?.[1] || null;
}

function base64ToBlob(base64, mimeType = 'application/pdf') {
  const clean = base64.includes(',') ? base64.split(',').pop() : base64;
  const binary = atob(clean);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mimeType });
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function readError(response) {
  const text = await response.text().catch(() => '');
  try {
    const json = JSON.parse(text);
    return json.detail || json.message || text || response.statusText;
  } catch {
    return text || response.statusText;
  }
}

export async function getHealth() {
  const response = await fetch(makeUrl('/health'));
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function analyzeDocuments({ fileV1, fileV2 }) {
  if (!fileV1 || !fileV2) {
    throw new Error('Нужно выбрать два PDF-файла: старую и новую версии документа.');
  }

  const formData = new FormData();
  formData.append('v1', fileV1);
  formData.append('v2', fileV2);

  const response = await fetch(makeUrl('/api/v1/generate-change-notice-json'), {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function generateChangeNoticePdf(payload) {
  const response = await fetch(makeUrl('/api/v1/generate-change-notice-pdf'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!response.ok) throw new Error(await readError(response));

  const contentType = response.headers.get('content-type') || '';
  const disposition = response.headers.get('content-disposition');

  if (contentType.includes('application/pdf')) {
    const blob = await response.blob();
    const filename = getFilenameFromDisposition(disposition) || `${payload.notice_id || 'change-notice'}.pdf`;
    downloadBlob(blob, filename);
    return { filename };
  }

  const data = await response.json();
  const base64 = data.pdf_base64 || data.base64 || data.file_base64 || data.content;
  if (!base64) return data;

  const filename = data.filename || `${payload.notice_id || 'change-notice'}.pdf`;
  downloadBlob(base64ToBlob(base64), filename);
  return { ...data, filename };
}
