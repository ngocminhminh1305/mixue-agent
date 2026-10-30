"""Local, conservative catalog ranking for OCR errors. Scores are not probabilities."""
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from functools import lru_cache

AUTO_SCORE = 0.86
AUTO_MARGIN = 0.10
AUTO_EDIT_RATIO = 0.88
SUGGEST_SCORE = 0.35


def folded(text):
    text = unicodedata.normalize('NFD', text.casefold().replace('đ', 'd'))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', text).split())


# Keep unavailable flavours here too: otherwise a retired flavour can be forced
# onto the closest available flavour. Long phrases protect variant identity.
MARKERS = (
    'matcha', 'mix', 'kiwi', 'khoai mon', 'viet quat', 'dau tay', 'xoai',
    'chanh', 'cam', 'dao', 'caramel', 'hat de', 'nguyen vi', 'trang',
    'duong den', 'kem matcha', 'kem mix', 'super', 'lucky', 'americano',
    'latte', 'mocha', 'tra den', 'tra xanh', 'tra sua', 'oc que', 'sundae',
    'lo hoi', 'thach dua', 'thach dao', 'thach duong den', 'oreo', 'o coco',
    'dao hong', 'dao vang', 'buff xanh', 'kem tuoi', 'hoa nhai', 'mat ong',
)


def markers(text):
    text = ' ' + text + ' '
    return frozenset(m for m in MARKERS if ' '+m+' ' in text)


class SimilarityMatcher:
    def __init__(self, products, surfaces):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.products = {p[0]: {'id': p[0], 'name': p[1], 'base': p[2], 'size': p[3]} for p in products}
        self.families = defaultdict(set)
        for product in self.products.values():
            self.families[product['base']].add(product['size'])
        self.surfaces = [(folded(text), owner) for text, owner in surfaces if folded(text)]
        self.vectorizer = TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 4), lowercase=False)
        self.matrix = self.vectorizer.fit_transform([text for text, _ in self.surfaces]) if self.surfaces else None

    def rank(self, base, size, available):
        from sklearn.metrics.pairwise import cosine_similarity
        query = folded(base)
        if not query or self.matrix is None:
            return []
        signature = markers(query)
        scores = cosine_similarity(self.vectorizer.transform([query]), self.matrix).ravel()
        best = {}
        for (surface, owner), value in zip(self.surfaces, scores):
            product = self.products[owner]
            if size is not None and product['size'] != size:
                continue
            # The unlabelled M rule includes inactive M variants. Do not select
            # their L counterpart just because only L is currently active.
            if size is None and product['size'] == 'L' and 'M' in self.families[product['base']]:
                continue
            candidate_markers = markers(surface)
            canonical_markers = markers(folded(product['base']))
            # Aliases cannot erase an explicit conflicting flavour or family.
            if not signature <= (candidate_markers | canonical_markers):
                continue
            score = float(value)
            if score < SUGGEST_SCORE:
                continue
            eligible = signature == candidate_markers
            edit = SequenceMatcher(None, query, surface, autojunk=False).ratio()
            candidate = {'product_id': owner, 'name': product['name'], 'score': score,
                         'available': owner in available,
                         'auto_eligible': eligible and edit >= AUTO_EDIT_RATIO}
            previous = best.get(owner)
            if previous is None or score > previous['score']:
                best[owner] = candidate
        return sorted(best.values(), key=lambda r: (-r['score'], r['product_id']))


@lru_cache(maxsize=8)
def cached_matcher(products, surfaces):
    # Full catalog names, aliases and sizes form the key, so setup edits never
    # leave a stale model. Active IDs are filtered separately for every lookup.
    return SimilarityMatcher(products, surfaces)


def approximate(base, size, index, available):
    ranked = index.similarity().rank(base, size, available)
    suggestions = [{'product_id': row['product_id'], 'name': row['name'], 'score': round(row['score'], 4)}
                   for row in ranked if row['available']][:3]
    margin = ranked[0]['score'] - (ranked[1]['score'] if len(ranked) > 1 else 0) if ranked else 0
    selected = ranked[0] if ranked and ranked[0]['available'] and ranked[0]['auto_eligible'] and ranked[0]['score'] >= AUTO_SCORE and margin >= AUTO_MARGIN else None
    return {'product_id': selected['product_id'] if selected else None,
            'method': 'approximate' if selected else 'review' if suggestions else 'unmatched',
            'score': round(ranked[0]['score'], 4) if ranked else None,
            'margin': round(margin, 4) if ranked else None, 'suggestions': suggestions}
