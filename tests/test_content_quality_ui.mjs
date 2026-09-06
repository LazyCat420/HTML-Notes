import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');

const INDEX_CSS = fs.readFileSync(path.join(ROOT, 'app', 'static', 'index.css'), 'utf8');
const INDEX_JS = fs.readFileSync(path.join(ROOT, 'app', 'static', 'index.js'), 'utf8');
const INDEX_HTML = fs.readFileSync(path.join(ROOT, 'app', 'static', 'index.html'), 'utf8');

test('index.css includes content quality and voting styles', () => {
    assert(INDEX_CSS.includes('.content-vote-actions'), 'Missing .content-vote-actions in index.css');
    assert(INDEX_CSS.includes('.vote-btn'), 'Missing .vote-btn in index.css');
    assert(INDEX_CSS.includes('.vote-btn.voted-up'), 'Missing .voted-up style');
    assert(INDEX_CSS.includes('.vote-btn.voted-down'), 'Missing .voted-down style');
    assert(INDEX_CSS.includes('.quality-flag-badge'), 'Missing .quality-flag-badge style');
    assert(INDEX_CSS.includes('.quality-toast'), 'Missing .quality-toast style');
});

test('index.js getCleanedCanvasHtml strips transient vote classes and toast', () => {
    assert(INDEX_JS.includes('item-downvoted-slight'), 'index.js should clean item-downvoted-slight');
    assert(INDEX_JS.includes('item-upvoted-highlight'), 'index.js should clean item-upvoted-highlight');
    assert(INDEX_JS.includes('.quality-toast'), 'index.js should remove .quality-toast elements');
});

test('index.js implements voteContent handler with delegation', () => {
    assert(INDEX_JS.includes('window.HN.voteContent'), 'window.HN.voteContent missing');
    assert(INDEX_JS.includes('showQualityToast'), 'showQualityToast helper missing');
    assert(INDEX_JS.includes('/quality/vote'), 'API call to /quality/vote missing');
});

test('index.html references bumped v3.4 script and v3.3 css for cache busting', () => {
    assert(INDEX_HTML.includes('index.css?v=3.3'), 'index.css version not updated');
    assert(INDEX_HTML.includes('index.js?v=3.4'), 'index.js version not updated');
});
