/* Small, optional enhancements; forms still submit through Django. */
(() => {
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  const upload = document.querySelector('[data-library-upload]');
  if (!upload) return;
  const input = upload.elements.file;
  const title = upload.elements.title;
  const zone = upload.querySelector('[data-upload-zone]');
  const selection = upload.querySelector('[data-upload-selection]');
  function chosen() {
    const file = input.files[0];
    if (!file) { selection.textContent = ''; return; }
    selection.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
    if (!title.value.trim()) title.value = file.name.replace(/\.[^.]+$/, '').slice(0,200);
  }
  input.addEventListener('change', chosen);
  ['dragenter', 'dragover'].forEach(type => zone.addEventListener(type, event => {
    event.preventDefault(); zone.classList.add('is-dragging');
  }));
  ['dragleave', 'drop'].forEach(type => zone.addEventListener(type, event => {
    event.preventDefault(); zone.classList.remove('is-dragging');
  }));
  zone.addEventListener('drop', event => {
    if (!event.dataTransfer?.files.length) return;
    if (event.dataTransfer.files.length !== 1) {
      selection.textContent = 'Choose one document at a time.';
      return;
    }
    input.files = event.dataTransfer.files;
    chosen();
  });
})();
