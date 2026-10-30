// DevMind's frontend only calls our own Python backend. Never put an API key here.
const messagesEl = document.querySelector('#messages');
const form = document.querySelector('#chat-form');
const input = document.querySelector('#chat-input');
const sendButton = document.querySelector('#send-button');
const attachButton = document.querySelector('#attach-button');
const fileInput = document.querySelector('#file-input');
const attachmentsEl = document.querySelector('#attachments');
const history = [];

// These limits mirror the ones in main.py. Keep them in sync.
const MAX_FILES = 5;
const MAX_FILE_MB = 15;
const ALLOWED_EXTENSIONS = [
  'png', 'jpg', 'jpeg', 'webp', 'gif',
  'mp4', 'mov', 'webm',
  'pdf', 'docx', 'txt', 'md', 'py', 'js', 'jsx', 'ts', 'tsx', 'html', 'css',
  'json', 'java', 'c', 'cpp', 'cs', 'go', 'rs', 'rb', 'php', 'xml', 'yml',
  'yaml', 'csv', 'sql', 'sh'
];

let busy = false;
let selectedFiles = [];
let previewUrls = [];

document.querySelector('#year').textContent = new Date().getFullYear();

/* --------------------------- helpers --------------------------- */

function fileExtension(name) {
  const dot = name.lastIndexOf('.');
  return dot === -1 ? '' : name.slice(dot + 1).toLowerCase();
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function shortLabel(file) {
  const ext = fileExtension(file.name).toUpperCase();
  return (ext || 'FILE').slice(0, 4);
}

/* -------------------------- messages -------------------------- */

function addMessage(role, text, isError = false) {
  const welcome = messagesEl.querySelector('.welcome');
  if (welcome) welcome.remove();
  const el = document.createElement('div');
  el.className = `message ${role}${isError ? ' error' : ''}`;
  el.textContent = text; // textContent prevents HTML injection from AI output.
  messagesEl.append(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

/* ------------------------ attachments ------------------------ */

function renderAttachments() {
  // Free the memory used by the previous thumbnails before rebuilding.
  previewUrls.forEach(url => URL.revokeObjectURL(url));
  previewUrls = [];
  attachmentsEl.innerHTML = '';
  attachmentsEl.hidden = selectedFiles.length === 0;

  selectedFiles.forEach((file, index) => {
    const chip = document.createElement('div');
    chip.className = 'attachment-chip';

    // Images get a real thumbnail so the user can confirm the right screenshot.
    if (file.type.startsWith('image/')) {
      const img = document.createElement('img');
      const url = URL.createObjectURL(file);
      previewUrls.push(url);
      img.src = url;
      img.alt = file.name;
      chip.append(img);
    } else {
      const badge = document.createElement('span');
      badge.className = 'attachment-badge';
      badge.textContent = shortLabel(file);
      chip.append(badge);
    }

    const info = document.createElement('span');
    info.className = 'attachment-info';
    const nameEl = document.createElement('b');
    nameEl.textContent = file.name;
    nameEl.title = file.name;
    const sizeEl = document.createElement('small');
    sizeEl.textContent = formatSize(file.size);
    info.append(nameEl, sizeEl);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'attachment-remove';
    remove.textContent = '×';
    remove.title = `Remove ${file.name}`;
    remove.setAttribute('aria-label', `Remove ${file.name}`);
    remove.addEventListener('click', () => {
      selectedFiles.splice(index, 1);
      renderAttachments();
    });

    chip.append(info, remove);
    attachmentsEl.append(chip);
  });
}

function addFiles(fileList) {
  for (const file of fileList) {
    if (selectedFiles.length >= MAX_FILES) {
      alert(`You can attach up to ${MAX_FILES} files per message.`);
      break;
    }
    const ext = fileExtension(file.name);
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      alert(`"${file.name}" is not a supported file type.`);
      continue;
    }
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      alert(`"${file.name}" is larger than ${MAX_FILE_MB} MB. Please compress it first.`);
      continue;
    }
    const alreadyAdded = selectedFiles.some(f => f.name === file.name && f.size === file.size);
    if (!alreadyAdded) selectedFiles.push(file);
  }
  renderAttachments();
}

function clearAttachments() {
  selectedFiles = [];
  fileInput.value = '';
  renderAttachments();
}

/* --------------------------- sending --------------------------- */

async function sendMessage(value, files = []) {
  const message = value.trim();
  if ((!message && files.length === 0) || busy) return;

  busy = true;
  sendButton.disabled = true;
  attachButton.disabled = true;
  input.value = '';
  input.style.height = '';

  const names = files.map(file => file.name);
  const shown = message || 'Please review the attached file(s).';
  addMessage('user', names.length ? `${shown}\n\nAttached: ${names.join(', ')}` : shown);
  clearAttachments();

  const pending = addMessage('assistant', 'Thinking…');

  try {
    let response;
    if (files.length) {
      // Attachments need multipart form data, not JSON, because they are binary.
      const body = new FormData();
      body.append('message', message);
      body.append('history', JSON.stringify(history.slice(-6)));
      files.forEach(file => body.append('files', file, file.name));
      response = await fetch('/api/upload', {method: 'POST', body});
    } else {
      response = await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message, history: history.slice(-6)})
      });
    }

    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Server error (${response.status})`);

    const answer = data.answer || 'The AI returned no text. Please try again.';
    pending.textContent = answer;
    history.push(
      {role: 'user', content: shown},
      {role: 'assistant', content: answer}
    );
  } catch (error) {
    pending.classList.add('error');
    pending.textContent = `Unable to get a response: ${error.message}. Check that the Python server is running and your .env contains a valid API key.`;
  } finally {
    busy = false;
    sendButton.disabled = false;
    attachButton.disabled = false;
    input.focus();
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

/* --------------------------- events --------------------------- */

form.addEventListener('submit', event => {
  event.preventDefault();
  sendMessage(input.value, selectedFiles.slice());
});

input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 115)}px`;
});

attachButton.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', () => {
  addFiles(fileInput.files);
  fileInput.value = ''; // lets the user pick the same file again later
});

document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => {
  document.querySelector('#assistant').scrollIntoView({behavior: 'smooth'});
  sendMessage(button.dataset.prompt);
}));

document.querySelector('#new-chat').addEventListener('click', () => {
  if (busy) return;
  history.length = 0;
  clearAttachments();
  messagesEl.innerHTML = '<div class="welcome"><div class="welcome-icon">✳</div><h3>Fresh start. What’s on your mind?</h3><p>Ask your next coding question below.</p></div>';
  input.focus();
});
