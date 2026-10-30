(() => {
  const get = id => document.getElementById(id);
  let rows = [], entries = {}, loadedDate = null;
  const dirty = () => Object.values(entries).some(value => value !== '');
  function feedback(text, error = false) {
    get('ice-cream-feedback').textContent = text;
    get('ice-cream-feedback').className = error ? 'notice negative' : 'hint';
  }
  function draw() {
    const body = get('ice-cream-rows');
    body.replaceChildren();
    for (const item of rows) {
      const tr = el('tr');
      tr.append(el('td', item.name), el('td', item.balance_known ? display(item.quantity, 'g') : "Chưa biết; ước tính " + display(item.ledger_estimate, 'g')),
                el('td', item.recorded ? display(item.total_used, 'g') : "Chưa ghi"));
      const td = el('td'), input = el('input');
      input.type = 'number'; input.min = '0'; input.max = '99999999999999.999999'; input.step = '0.000001';
      input.value = entries[item.id] ?? ''; input.placeholder = "Giữ nguyên";
      input.setAttribute('aria-label', "Lượng bột làm kem trong ngày: " + item.name);
      const action = el('td'), save = el('button', "Lưu dòng này"); save.type = 'button';
      save.disabled = busy || input.value === '';
      input.oninput = () => { entries[item.id] = input.value; save.disabled = busy || input.value === ''; updateButton(); };
      save.onclick = () => { if (input.reportValidity()) saveTotals([item.id]); };
      td.append(input); action.append(save); tr.append(td, action); body.append(tr);
    }
    if (!rows.length) {
      const tr = el('tr'), td = el('td', "Chưa có loại bột ghi theo ngày. Bật tại Danh mục & công thức.");
      td.colSpan = 5; tr.append(td); body.append(tr);
    }
    updateButton();
  }
  function updateButton() { get('ice-cream-save').disabled = busy || !dirty() || !loadedDate; }
  async function loadDate() {
    if (busy) return;
    if (dirty() && !confirm("Bỏ lượng bột chưa lưu và tải lại ngày này?")) return;
    if (!get('ice-cream-date').reportValidity()) return;
    setBusy(true); rows = []; entries = {}; loadedDate = null; draw();
    try {
      const data = await request('/api/inventory/ice-cream-usage?' + new URLSearchParams({used_on: get('ice-cream-date').value}));
      loadedDate = data.used_on; rows = data.items;
      feedback(data.legacy_automatic_sales ? "Ngày này có báo cáo cũ đã tự trừ bột kem. Hoàn tác và duyệt lại theo cách ghi bột thực tế trước khi nhập." : "Đã tải lượng bột ngày " + loadedDate + '.');
    } catch (error) { feedback(error.message, true); }
    finally { setBusy(false); draw(); }
  }
  async function saveTotals(ids = null) {
    if (busy) return;
    if (get('ice-cream-date').value !== loadedDate) { feedback("Chọn Xem theo ngày trước khi lưu.", true); return; }
    if (!get('ice-cream-note').reportValidity() || !get('ice-cream-note').value.trim()) return;
    const selected = rows.filter(item => entries[item.id] !== undefined && entries[item.id] !== '' && (!ids || ids.includes(item.id)));
    if (!selected.length) return;
    const payload = {used_on: loadedDate, note: get('ice-cream-note').value.trim(), items: selected.map(item => ({
      item_id: item.id, balance_token: item.balance_token, usage_token: item.usage_token, total_used: entries[item.id]
    }))};
    const request_id = requestId('ice_cream_usage', payload);
    setBusy(true); updateButton();
    try {
      const result = await request('/api/inventory/ice-cream-usage', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...payload, request_id})});
      clearRequestId('ice_cream_usage', payload);
      const updated = new Map(result.items.map(item => [item.id, item]));
      rows = rows.map(item => updated.get(item.id) || item);
      selected.forEach(item => delete entries[item.id]);
      feedback(result.replayed ? "Đã lưu trước đó; không trừ kho thêm." : "Đã lưu tổng trong ngày. Chỉ phần chênh lệch được cập nhật vào kho.");
      await loadHistory();
    } catch (error) { feedback(error.message + " Tải lại bảng bột trước khi sửa dòng đã thay đổi.", true); }
    finally { setBusy(false); draw(); }
  }
  get('ice-cream-date').value = today();
  get('ice-cream-load').onclick = loadDate;
  get('ice-cream-form').onsubmit = event => { event.preventDefault(); saveTotals(); };
  window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
  // The normal inventory initializer also uses busy; start after its initial load.
  async function initialize() {
    if (busy) { setTimeout(initialize, 100); return; }
    await loadDate();
  }
  initialize();
})();
