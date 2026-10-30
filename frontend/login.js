(() => {
  const form = document.getElementById('login-form');
  const button = document.getElementById('login-submit');
  const status = document.getElementById('login-status');
  let csrf = '';
  async function prepare() {
    try {
      const response = await fetch('/api/auth/login', {credentials: 'same-origin', cache: 'no-store'});
      const body = await response.json();
      if (!response.ok) throw Error(body.detail || 'Chưa kết nối được máy chủ.');
      csrf = body.csrf_token;
      button.disabled = false;
      status.textContent = '';
    } catch (error) { status.textContent = error.message; }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    button.disabled = true;
    status.textContent = 'Đang đăng nhập…';
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify({username: form.username.value, password: form.password.value,
          next: new URLSearchParams(location.search).get('next') || '/'})
      });
      const body = await response.json();
      form.password.value = '';
      if (!response.ok) throw Error(body.detail || 'Chưa đăng nhập được.');
      location.replace(body.next);
    } catch (error) {
      status.textContent = error.message || 'Mất kết nối. Vui lòng thử lại.';
      button.disabled = false;
      // Refresh the short-lived login challenge for a manual retry.
      try {
        const response = await fetch('/api/auth/login', {cache: 'no-store'});
        if (response.ok) csrf = (await response.json()).csrf_token;
      } catch (_) { /* Keep the message above. */ }
    }
  });
  prepare();
})();
