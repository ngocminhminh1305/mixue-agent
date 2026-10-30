# Frontend

The app keeps its existing FastAPI-served HTML and JavaScript. No frontend build step, external fonts, component framework or new frontend dependency is required.

`frontend/app.css` owns the shared light theme, navigation, controls, tables, responsive layouts and per-page styles. It uses white surfaces, near-black primary actions and restrained mint, lavender and peach accents. Pages declare their active navigation item in HTML so navigation remains usable without JavaScript.

`frontend/ui.js` contains presentation helpers for report status labels, displayed units, common review warnings and the upload drop zone. It also updates the upload step indicator. These helpers do not rename products, modify stored receipt text, translate API enum values or alter model answers.

All six pages use Vietnamese interface labels and `vi-VN` number/date display. Original shop data, notes and historical records remain unchanged; user-entered notes and some provider error details can retain their original language. Backend status values such as `draft` and `approved`, stock units such as `piece`, and action values such as `purchase` stay unchanged in payloads.

On narrow screens all six navigation destinations stay visible. Wide data tables scroll inside their containers. Inventory entry and history tables have bounded scrolling. Keyboard focus, labelled controls, a skip link and reduced-motion support are included. Existing confirmation and approval checks are retained.

Approved ingredient calculations and long warning lists can be expanded when needed. New consumption previews expand the calculation so the approval controls remain visible. Editing a catalog field enables Save immediately while typing.

## Running and verification

Start the backend using the existing environment and Uvicorn command. The shared assets are served at `/assets/app.css` and `/assets/ui.js`. Reload the browser after updates; restart the backend if new asset routes have not been loaded.

Visual checks covered all six pages at desktop and phone widths, with no page-wide horizontal overflow or browser JavaScript errors. Browser workflow checks used intercepted API responses for all writes and model requests, covering upload/drop, extraction, saving a draft, similarity suggestions, manual review, confirmation-gated approval, four inventory operations, daily ice-cream powder entry, catalog edits and assistant replies. Live database writes and model requests were not used for those checks.


## Page organization

The root `/` opens Tổng quan; `/dashboard` remains an alias. Nhập doanh số is at `/upload`. Saved sales are labelled Hoá đơn đã lưu. Cập nhật tồn kho has four hash-addressable sections: nhập hàng, kiểm đếm (with a known-waste submode), mức nhắc, and lịch sử. History filters use the entered purchase date for purchases and the recorded local date for counts, waste and thresholds. The separate movement view retains its recorded-time basis.

The catalog separates purchased ingredients/packaging and prepared stock into different views of the same stock array; it does not duplicate or migrate data. Preparation recipes are labelled Công thức pha chế.

Powder entry is the first upload step. Bột kem nguyên vị is mandatory (zero is permitted); optional flagged powders such as matcha can be added. The date comes from the receipt date field, not a separate powder date input. Saving sends the receipt and daily powder request in one multipart request. The backend validates date equality and commits both in one transaction; stale stock tokens or other failures roll back both and clean up the new photo. Subsequent photos for the day update the same daily total rather than accumulating it. Original standalone powder API endpoints remain compatible.
