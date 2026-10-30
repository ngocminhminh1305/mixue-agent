// Daily totals travel with the receipt; the backend commits both atomically.
(() => {
  const get = id => document.getElementById(id);
  let powders = [], selected = [], busy = false, receiptDate = null, version = 0, dirty = false;
  const values = new Map();
  function feedback(message, error = false) {
    get('receipt-powder-feedback').textContent = message;
    get('receipt-powder-feedback').className = error ? 'issue' : 'hint';
  }
  async function request(url) {
    const r = await fetch(url); const data = await r.json();
    if (!r.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Chưa tải được danh mục bột.');
    return data;
  }
  function draw() {
    const body = get('receipt-powder-rows'); body.replaceChildren();
    selected.forEach((id, index) => {
      const item = powders.find(p => p.id === id);
      const row = document.createElement('div'); row.className = 'powder-line';
      const label = document.createElement('label'); label.htmlFor = 'powder-' + id;
      label.textContent = item.name + (index === 0 ? ' · bắt buộc' : '');
      const input = document.createElement('input'); input.type = 'number'; input.id = label.htmlFor;
      input.min = '0'; input.step = '0.000001'; input.max = '99999999999999.999999';
      input.placeholder = 'Nhập tổng gram'; input.required = true; input.disabled = busy;
      input.value = values.get(id) ?? '';
      input.oninput = () => { values.set(id, input.value); dirty = true; };
      const unit = document.createElement('span'); unit.className = 'powder-unit'; unit.textContent = 'g';
      const field = document.createElement('div'); field.className = 'powder-input'; field.append(input, unit);
      row.append(label, field);
      if (index > 0) {
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary';
        remove.textContent = 'Bỏ'; remove.disabled = busy; remove.setAttribute('aria-label','Bỏ '+item.name);
        remove.onclick = () => { selected = selected.filter(x => x !== id); values.delete(id); dirty = true; draw(); };
        row.append(remove);
      }
      body.append(row);
    });
    const choice = get('receipt-powder-add'); choice.replaceChildren();
    const blank = document.createElement('option'); blank.value = ''; blank.textContent = '+ Thêm loại bột khác'; choice.append(blank);
    powders.filter(p => !selected.includes(p.id)).forEach(p => {
      const option = document.createElement('option'); option.value = p.id; option.textContent = p.name; choice.append(option);
    });
    choice.disabled = busy || selected.length === powders.length;
  }
  const ready = request('/api/shop-config').then(config => {
    powders = config.stock_items.filter(p => p.ice_cream_powder && p.kind === 'raw' && p.unit === 'g');
    const original = powders.find(p => p.name.toLocaleLowerCase('vi').normalize('NFC') === 'bột kem nguyên vị')
      || powders.find(p => /bột kem/i.test(p.name));
    if (!original) throw Error('Thiếu Bột kem nguyên vị. Thêm nguyên liệu và bật ghi bột làm kem trong Danh mục & công thức.');
    selected = [original.id]; values.set(original.id, ''); draw(); return true;
  }).catch(error => { feedback(error.message, true); return false; });
  get('receipt-powder-add').onchange = event => {
    const id = event.target.value; if (!id || busy) return;
    selected.push(id); values.set(id, ''); dirty = true; draw(); get('powder-' + id)?.focus();
  };
  async function validate() {
    if (!await ready) return false;
    for (const id of selected) {
      const input = get('powder-' + id);
      if (!input.reportValidity() || input.value === '') {
        feedback('Nhập lượng bột trước khi trích xuất. Nếu không dùng trong ngày, nhập 0.', true);
        input.focus(); return false;
      }
    }
    return true;
  }
  async function syncDate(day) {
    receiptDate = day || null;
    const current = ++version;
    get('receipt-powder-date').textContent = day ? 'Ngày dùng bột: ' + day.split('-').reverse().join('/') + ' · lấy từ hoá đơn' : 'Ngày sử dụng sẽ lấy từ ảnh hoá đơn.';
    if (!day || !await ready) return;
    try {
      const data = await request('/api/inventory/ice-cream-usage?used_on=' + encodeURIComponent(day));
      if (current !== version) return;
      const saved = data.items.filter(p => p.recorded);
      feedback(saved.length ? 'Ngày này đã có lượng bột được lưu. Nhập tổng cả ngày; lưu lại cùng tổng sẽ không trừ thêm.' : 'Lượng bột và bản nháp sẽ được lưu cùng lúc.');
    } catch (error) { if (current === version) feedback(error.message, true); }
  }
  window.ReceiptPowders = {
    validate, syncDate,
    setBusy(value) { busy = value; get('receipt-powder-section').querySelectorAll('input,select,button').forEach(n => n.disabled = value); if (!value) draw(); },
    async payloadFor(day) {
      if (!await validate()) throw Error('Kiểm tra lại lượng bột làm kem.');
      if (!day) throw Error('Chưa có ngày trên hoá đơn.');
      if (receiptDate !== day) await syncDate(day);
      const data = await request('/api/inventory/ice-cream-usage?used_on=' + encodeURIComponent(day));
      const rows = new Map(data.items.map(p => [p.id, p]));
      const items = selected.map(id => {
        const row = rows.get(id);
        if (!row) throw Error('Danh mục bột đã thay đổi. Tải lại trang trước khi lưu.');
        return {item_id: id, total_used: values.get(id), balance_token: row.balance_token, usage_token: row.usage_token};
      });
      return {request_id: crypto.randomUUID(), used_on: day, note: 'Bột làm kem thực tế theo ngày hoá đơn', items};
    },
    saved() { dirty = false; feedback('Đã lưu lượng bột theo ngày trên hoá đơn. Lưu ảnh khác cùng ngày không cộng lại tổng bột.'); }
  };
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
})();
