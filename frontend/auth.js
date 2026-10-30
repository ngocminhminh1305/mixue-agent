// Loaded before page scripts: every same-origin write receives a session CSRF token.
(() => {
  const nativeFetch = window.fetch.bind(window);
  let redirecting = false;
  function login() {
    if (redirecting) return;
    redirecting = true;
    location.replace('/login?' + new URLSearchParams({next: location.pathname + location.search + location.hash}));
  }
  async function readSession() {
    const response = await nativeFetch('/api/auth/session', {credentials: 'same-origin', cache: 'no-store'});
    if (response.status === 401) login();
    const body = await response.json();
    if (!response.ok) throw Error(body.detail || 'Chưa xác nhận được phiên đăng nhập.');
    return body;
  }
  const session = readSession();
  // The page can still display network errors without an unhandled rejection.
  session.catch(() => {});
  window.fetch = async (input, init = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    const method = (init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (url.origin === location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
      headers.set('X-CSRF-Token', (await session).csrf_token);
      init = {...init, headers};
    }
    const response = await nativeFetch(input, init);
    if (url.origin === location.origin && response.status === 401) login();
    return response;
  };
  document.addEventListener('DOMContentLoaded', async () => {
    const footer = document.querySelector('.nav-footer');
    if (!footer) return;
    try {
      const account = await session;
      const name = document.createElement('strong');
      name.textContent = account.username;
      const logout = document.createElement('button');
      logout.type = 'button'; logout.className = 'secondary logout-button'; logout.textContent = 'Đăng xuất';
      const message = document.createElement('span'); message.setAttribute('role', 'status');
      logout.addEventListener('click', async () => {
        logout.disabled = true;
        try {
          const response = await fetch('/api/auth/logout', {method: 'POST'});
          if (!response.ok) throw Error('Chưa đăng xuất được. Vui lòng thử lại.');
          location.replace('/login');
        } catch (error) { message.textContent = error.message; logout.disabled = false; }
      });
      footer.replaceChildren(name, logout, message);
    } catch (error) { footer.textContent = error.message; }
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) {
      document.documentElement.style.visibility = 'hidden';
      readSession().then(() => location.reload()).catch(login);
    }
  });
})();
