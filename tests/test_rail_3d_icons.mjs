import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

import { REPO_SHAPES, resolveRepoShape } from '../app/static/js/rail-3d-icons.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('every live and workspace repo maps to a distinct 3D monochrome silhouette', () => {
    const liveRepos = [
        'braindeadbot-client',
        'doc-client',
        'html-notes',
        'music-player',
        'portal-client',
        'prism-client',
        'spark-console',
        'trading-client',
        'youtube-wallgarden',
        'drift-king',
        'pinball-knight',
        'treesearch-client',
    ];
    const resolved = liveRepos.map((id) => resolveRepoShape(id));
    assert.equal(new Set(resolved).size, liveRepos.length, `expected 12 unique shapes, got ${resolved}`);
    assert.ok(!resolved.includes('octa'), 'known repos must not fall back to generic octahedron');
    assert.equal(resolveRepoShape('unknown-custom-service'), 'octa');
    assert.equal(resolveRepoShape('HTML-NOTES'), REPO_SHAPES['html-notes']);
});

test('index.html pre-paint script migrates unset/legacy defaults to brute + 3d and respects explicit overrides', () => {
    const html = fs.readFileSync(path.join(ROOT, 'app/static/index.html'), 'utf8');
    const match = html.match(/<script>\s*([\s\S]*?html_notes_theme_v2[\s\S]*?)<\/script>/);
    assert.ok(match, 'pre-paint theme migration script missing in index.html');
    const scriptBody = match[1];

    function runPrePaint(initialStore) {
        const store = new Map(Object.entries(initialStore));
        const attrs = {};
        const sandbox = {
            localStorage: {
                getItem: (k) => (store.has(k) ? store.get(k) : null),
                setItem: (k, v) => store.set(k, String(v)),
            },
            document: {
                documentElement: {
                    setAttribute: (k, v) => { attrs[k] = v; },
                },
            },
        };
        vm.runInNewContext(scriptBody, sandbox);
        return { store: Object.fromEntries(store), attrs };
    }

    // Fresh visitor -> brute + 3d
    const fresh = runPrePaint({});
    assert.equal(fresh.attrs['data-theme'], 'brute');
    assert.equal(fresh.store.html_notes_theme, 'brute');
    assert.equal(fresh.store.html_notes_icon_style, '3d');
    assert.equal(fresh.store.html_notes_theme_v2, '1');

    // Legacy visitor who had default hud/vector persisted -> promoted once to brute + 3d
    const legacy = runPrePaint({ html_notes_theme: 'hud', html_notes_icon_style: 'vector' });
    assert.equal(legacy.attrs['data-theme'], 'brute');
    assert.equal(legacy.store.html_notes_icon_style, '3d');

    // Visitor who already picked a custom theme (e.g. forest) keeps it
    const custom = runPrePaint({ html_notes_theme: 'forest', html_notes_icon_style: 'mono' });
    assert.equal(custom.attrs['data-theme'], 'forest');
    assert.equal(custom.store.html_notes_icon_style, 'mono');

    // After v2 migration, explicitly switching back to hud stays hud
    const explicitHud = runPrePaint({
        html_notes_theme_v2: '1',
        html_notes_theme: 'hud',
        html_notes_icon_style: 'vector',
    });
    assert.equal(explicitHud.attrs['data-theme'], undefined);
    assert.equal(explicitHud.store.html_notes_theme, 'hud');
    assert.equal(explicitHud.store.html_notes_icon_style, 'vector');
});
