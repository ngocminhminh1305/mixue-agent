# Receipt product matching

Matching runs in Python after image extraction and when “Match unmatched products” is selected. No additional Gemini/OpenRouter call is made. The original receipt name stays visible and unchanged.

## Selection order

1. Existing exact rules: Unicode normalization, case folding, whitespace, removal of `(MT)`, catalog aliases and bare topping names. Size L is explicit; an unlabelled two-size product means M. A unique L-only product can still match an unlabelled receipt name.
2. Unmatched names are ranked against catalog names and their aliases using character TF-IDF (2–4 character fragments inside word boundaries) and cosine similarity. The secondary comparison tolerates Vietnamese accent and punctuation differences. Exact comparison still retains accents.
3. An approximate candidate is selected only if its cosine score is at least 0.86, its lead over the next distinct product is at least 0.10, its character edit ratio is at least 0.88, and its protected variant markers agree. These are initial conservative thresholds tested against supplied OCR errors, not calibrated probabilities of correctness.
4. Otherwise up to three compatible suggestions with scores of at least 0.35 are shown. Selecting one is an explicit manual edit that must be saved. An unmatched row still prevents approval.

Constants and variant markers are in `bobaops/sales/similarity.py`. Exact matching remains available separately through `bobaops.sales.routes.match_product`; draft ingestion/rematching use `bobaops.sales.routes.resolve_product`.

## Guards and review

- Explicit M/L filters candidates. A size that looks corrupted, such as `(Slze L)`, is not silently interpreted as an unlabelled M.
- Inactive products are never selected or suggested, but their similarity scores can block a nearby active guess. Inactive M also prevents a mistaken default to L.
- Exact ambiguities or exact unavailable identities never fall through to fuzzy selection.
- Known flavour/family markers distinguish Matcha/Mix, white/original boba, Super/Lucky, fruit variants and retired kiwi/khoai môn flavours. Missing variant information requires review. Update these guards and regression tests when the menu gains new distinguishing flavours.
- Names outside the menu can remain unmatched or receive low-confidence review suggestions. Similarity alone does not establish that the shop sells a receipt item.
- Existing manual selections are preserved by rematching. Duplicate-product checks and inventory approval rules still apply.
- Approximate selections are annotated in the draft. Approved/reversed reports keep their frozen selections and are not re-ranked against the current menu.

The cached vectorizer is keyed by full catalog names, sizes and aliases. Catalog edits refresh the comparison model; current availability is checked on every lookup. Scores and suggestions are derived review information, not changes to the product catalog or receipt names.

## Setup and checks

Install `requirements.txt` in the existing project virtual environment, then restart Uvicorn. No database migration is needed. Reload an existing draft and click “Match unmatched products” to apply the matcher to its remaining rows. New uploads use it automatically.

Tests cover accent/character errors, topping aliases, exact priority, M/L rules, inactive candidates, ambiguity, retired flavours, refreshed catalogs and upload → manual review → approval. The regression examples include `Dương Chỉ Cam Lộ`, `Trà Sữa Ba Vương` and `Lucky Sundae Ô-coco`.
