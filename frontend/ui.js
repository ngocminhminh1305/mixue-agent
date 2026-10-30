// Presentation helpers only. Receipt names, model answers and API payloads stay intact.
(() => {
  const statuses = {draft: 'Bản nháp', approved: 'Đã duyệt', reversed: 'Đã hoàn tác', missing: 'Chưa có báo cáo'};
  const messages = {
    'Choose an available product from the catalog.': 'Chọn sản phẩm phù hợp trong danh mục.',
    'Enter the quantity.': 'Nhập số lượng.',
    'Enter an item name.': 'Nhập tên sản phẩm.',
    'Possible photo overlap: compare the source photos and remove duplicate rows. Quantities have not been added together.': 'Có thể trùng giữa các ảnh. Đối chiếu và xóa dòng trùng; số lượng chưa được cộng gộp.',
    'Evidence that the report continues outside the photo at the bottom.': 'Báo cáo có thể còn phần bên dưới ảnh. Kiểm tra và tải thêm ảnh nếu cần.',
    'The report has no sales rows.': 'Báo cáo chưa có dòng bán hàng.',
    'No product rows were extracted.': 'Chưa đọc được dòng sản phẩm nào.',
    'Item name needs review.': 'Kiểm tra lại tên sản phẩm.',
    'Quantity needs review.': 'Kiểm tra lại số lượng.',
    'Report date needs review.': 'Kiểm tra lại ngày báo cáo.',
    'Duplicate item name; review before importing.': 'Tên sản phẩm trùng; đối chiếu trước khi lưu.'
  };
  window.BobaUI = {
    unit: value => value === 'piece' ? 'cái' : value,
    status: value => statuses[value] || value,
    text(value) {
      if (typeof value !== 'string') return value;
      if (messages[value]) return messages[value];
      if (statuses[value]) return statuses[value];
      return value
        .replace(/^(.+): starting stock is unknown\..*$/, '$1: tồn ban đầu chưa xác nhận. Kiểm đếm để có số tồn chính xác.')
        .replace(/^(.+): ledger balance after deduction is negative \((.+)\)\.$/, '$1: tồn sau khi trừ đang âm ($2).')
        .replace(/^(.+): powders are recorded separately.*$/, '$1: bột làm kem được ghi riêng theo lượng thực tế mỗi ngày, không trừ từ doanh số.')
        .replace(/^Recipe: /, 'Công thức tạm tính: ')
        .replace(/^Ingredient: /, 'Nguyên liệu tạm tính: ')
        .replace(/^Preparation yield: /, 'Sản lượng mẻ tạm tính: ')
        .replace(/^Selling price: /, 'Giá bán tạm tính: ');
    }
  };
  const zone = document.querySelector('.upload-zone');
  const input = document.getElementById('image');
  if (zone && input) {
    const result = document.getElementById('result');
    const updateStep = () => document.querySelectorAll('.flow-step').forEach((step, index) => {
      const active = index === (result.hidden ? (input.files.length ? 1 : 0) : 2);
      step.classList.toggle('active', active);
      if (active) step.setAttribute('aria-current', 'step');
      else step.removeAttribute('aria-current');
    });
    new MutationObserver(updateStep).observe(result, {attributes: true, attributeFilter: ['hidden']});
    updateStep();
    input.addEventListener('change', () => {
      document.getElementById('file-name').textContent = input.files[0]?.name || '';
      updateStep();
    });
    for (const event of ['dragenter', 'dragover']) zone.addEventListener(event, e => {
      e.preventDefault(); if (!input.disabled) zone.classList.add('drag-over');
    });
    for (const event of ['dragleave', 'drop']) zone.addEventListener(event, e => {
      e.preventDefault(); zone.classList.remove('drag-over');
    });
    zone.addEventListener('drop', e => {
      if (input.disabled || !e.dataTransfer?.files.length) return;
      const file = e.dataTransfer.files[0];
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) return;
      const files = new DataTransfer(); files.items.add(file); input.files = files.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
    });
  }
})();
