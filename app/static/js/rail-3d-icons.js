/**
 * Three.js 3-Tone Toon Ink spinning 3D repo icons for #app-rail.
 *
 * Uses ONE shared WebGLRenderer (<canvas class="rail-3d-canvas">) with
 * per-icon setViewport/setScissor cells so 12+ spinning 3D repo silhouettes
 * consume only a single WebGL context, pause when hidden, respect
 * prefers-reduced-motion, and fall back gracefully when WebGL is unavailable.
 */
import * as THREE from '../lib/three.module.min.js';

export const REPO_SHAPES = Object.freeze({
    'html-notes': 'notes',
    'doc-client': 'book',
    'trading-client': 'trading',
    'braindeadbot-client': 'bot',
    'music-player': 'cassette',
    'youtube-wallgarden': 'crt',
    'portal-client': 'portal',
    'prism-client': 'prism',
    'spark-console': 'spark',
    'drift-king': 'car',
    'pinball-knight': 'knight',
    'treesearch-client': 'tree',
});

export function resolveRepoShape(appId) {
    const key = String(appId || '').trim().toLowerCase();
    if (REPO_SHAPES[key]) return REPO_SHAPES[key];
    for (const [prefix, shape] of Object.entries(REPO_SHAPES)) {
        if (key.includes(prefix.replace('-client', ''))) return shape;
    }
    return 'octa';
}

function createThreeToneGradient() {
    // 3 stark monochrome toon stops: deep shadow (#121212), mid gray (#787878), crisp white (#f8f8f8)
    const data = new Uint8Array([
        18, 18, 18, 255,
        120, 120, 120, 255,
        248, 248, 248, 255,
    ]);
    const tex = new THREE.DataTexture(data, 3, 1, THREE.RGBAFormat);
    tex.minFilter = THREE.NearestFilter;
    tex.magFilter = THREE.NearestFilter;
    tex.generateMipmaps = false;
    tex.needsUpdate = true;
    return tex;
}

function addPart(group, geo, toonMat, edgeMat, pos, rot) {
    const mesh = new THREE.Mesh(geo, toonMat);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 24), edgeMat);
    mesh.add(edges);
    if (pos) mesh.position.set(pos[0], pos[1], pos[2]);
    if (rot) mesh.rotation.set(rot[0], rot[1], rot[2]);
    group.add(mesh);
    return mesh;
}

function buildExtrudedShape(points, depth) {
    const shape = new THREE.Shape();
    points.forEach(([x, y], idx) => {
        if (idx === 0) shape.moveTo(x, y);
        else shape.lineTo(x, y);
    });
    shape.closePath();
    const geo = new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: false });
    geo.translate(0, 0, -depth / 2);
    return geo;
}

function buildNotesGroup(tm, em) {
    const g = new THREE.Group();
    const slab = new THREE.BoxGeometry(1.12, 0.14, 0.86);
    addPart(g, slab, tm, em, [-0.16, -0.34, -0.18], [0, -0.14, 0.06]);
    addPart(g, slab.clone(), tm, em, [0, 0, 0], [0, 0, 0]);
    addPart(g, slab.clone(), tm, em, [0.16, 0.34, 0.18], [0, 0.14, -0.06]);
    return g;
}

function buildBookGroup(tm, em) {
    const g = new THREE.Group();
    addPart(g, new THREE.BoxGeometry(0.18, 1.18, 0.92), tm, em, [-0.46, 0, 0]);
    addPart(g, new THREE.BoxGeometry(0.92, 0.14, 0.86), tm, em, [0.06, -0.50, 0]);
    addPart(g, new THREE.BoxGeometry(0.84, 0.72, 0.76), tm, em, [0.04, -0.04, 0]);
    addPart(g, new THREE.BoxGeometry(0.92, 0.14, 0.86), tm, em, [0.06, 0.44, 0], [0, 0, 0.12]);
    return g;
}

function buildTradingGroup(tm, em) {
    const g = new THREE.Group();
    addPart(g, new THREE.BoxGeometry(1.52, 0.16, 0.56), tm, em, [0, -0.62, 0]);
    addPart(g, new THREE.BoxGeometry(0.32, 0.58, 0.34), tm, em, [-0.46, -0.25, 0]);
    addPart(g, new THREE.BoxGeometry(0.32, 0.96, 0.34), tm, em, [0, -0.06, 0]);
    addPart(g, new THREE.BoxGeometry(0.32, 1.40, 0.34), tm, em, [0.46, 0.16, 0]);
    return g;
}

function buildBotGroup(tm, em) {
    const g = new THREE.Group();
    addPart(g, new THREE.BoxGeometry(1.12, 0.90, 0.88), tm, em, [0, -0.06, 0]);
    addPart(g, new THREE.BoxGeometry(0.84, 0.24, 0.16), tm, em, [0, 0.06, 0.48]);
    addPart(g, new THREE.BoxGeometry(0.16, 0.32, 0.16), tm, em, [0, 0.56, 0]);
    addPart(g, new THREE.BoxGeometry(0.30, 0.18, 0.30), tm, em, [0, 0.78, 0]);
    addPart(g, new THREE.BoxGeometry(0.16, 0.36, 0.30), tm, em, [-0.64, -0.06, 0]);
    addPart(g, new THREE.BoxGeometry(0.16, 0.36, 0.30), tm, em, [0.64, -0.06, 0]);
    return g;
}

function buildCassetteGroup(tm, em) {
    const g = new THREE.Group();
    addPart(g, new THREE.BoxGeometry(1.38, 0.88, 0.34), tm, em, [0, 0, 0]);
    const spool = new THREE.CylinderGeometry(0.20, 0.20, 0.44, 8);
    addPart(g, spool, tm, em, [-0.32, 0.06, 0], [Math.PI / 2, 0, 0]);
    addPart(g, spool.clone(), tm, em, [0.32, 0.06, 0], [Math.PI / 2, 0, 0]);
    addPart(g, new THREE.BoxGeometry(0.86, 0.22, 0.40), tm, em, [0, -0.32, 0]);
    return g;
}

function buildCrtGroup(tm, em) {
    const g = new THREE.Group();
    addPart(g, new THREE.BoxGeometry(1.24, 0.96, 0.84), tm, em, [0, 0.04, -0.06]);
    addPart(g, new THREE.BoxGeometry(0.72, 0.18, 0.52), tm, em, [0, -0.54, -0.06]);
    const play = buildExtrudedShape([[-0.22, -0.26], [0.30, 0], [-0.22, 0.26]], 0.20);
    addPart(g, play, tm, em, [0, 0.04, 0.46]);
    return g;
}

function buildSparkGroup(tm, em) {
    const g = new THREE.Group();
    const bolt = buildExtrudedShape([
        [0.10, 0.78], [-0.42, 0.02], [-0.04, 0.02],
        [-0.22, -0.78], [0.44, -0.04], [0.06, -0.04],
    ], 0.30);
    addPart(g, bolt, tm, em, [0, 0, 0]);
    return g;
}

function buildCarGroup(tm, em) {
    const g = new THREE.Group();
    addPart(g, new THREE.BoxGeometry(1.42, 0.34, 0.74), tm, em, [0, -0.14, 0]);
    addPart(g, new THREE.BoxGeometry(0.74, 0.30, 0.64), tm, em, [-0.08, 0.18, 0]);
    const wheel = new THREE.CylinderGeometry(0.20, 0.20, 0.86, 8);
    addPart(g, wheel, tm, em, [-0.42, -0.30, 0], [Math.PI / 2, 0, 0]);
    addPart(g, wheel.clone(), tm, em, [0.42, -0.30, 0], [Math.PI / 2, 0, 0]);
    return g;
}

function buildKnightGroup(tm, em) {
    const g = new THREE.Group();
    const shield = buildExtrudedShape([
        [-0.48, 0.62], [0.48, 0.62], [0.48, 0.0], [0, -0.68], [-0.48, 0.0],
    ], 0.28);
    addPart(g, shield, tm, em, [0, 0, -0.06]);
    addPart(g, new THREE.IcosahedronGeometry(0.26, 0), tm, em, [0, 0.08, 0.30]);
    return g;
}

function buildTreeGroup(tm, em) {
    const g = new THREE.Group();
    const node = new THREE.BoxGeometry(0.36, 0.36, 0.36);
    addPart(g, node, tm, em, [0, 0.54, 0]);
    addPart(g, node.clone(), tm, em, [-0.46, -0.38, 0.14]);
    addPart(g, node.clone(), tm, em, [0.46, -0.38, -0.14]);
    addPart(g, new THREE.BoxGeometry(0.12, 0.62, 0.12), tm, em, [-0.22, 0.08, 0.06], [0, 0, -0.44]);
    addPart(g, new THREE.BoxGeometry(0.12, 0.62, 0.12), tm, em, [0.22, 0.08, -0.06], [0, 0, 0.44]);
    return g;
}

function buildRepoGroup(shape, toonMat, edgeMat) {
    switch (shape) {
        case 'notes': return buildNotesGroup(toonMat, edgeMat);
        case 'book': return buildBookGroup(toonMat, edgeMat);
        case 'trading': return buildTradingGroup(toonMat, edgeMat);
        case 'bot': return buildBotGroup(toonMat, edgeMat);
        case 'cassette': return buildCassetteGroup(toonMat, edgeMat);
        case 'crt': return buildCrtGroup(toonMat, edgeMat);
        case 'portal': {
            const g = new THREE.Group();
            addPart(g, new THREE.TorusGeometry(0.60, 0.22, 6, 10), toonMat, edgeMat);
            return g;
        }
        case 'prism': {
            const g = new THREE.Group();
            addPart(g, new THREE.CylinderGeometry(0.76, 0.76, 1.18, 3), toonMat, edgeMat);
            return g;
        }
        case 'spark': return buildSparkGroup(toonMat, edgeMat);
        case 'car': return buildCarGroup(toonMat, edgeMat);
        case 'knight': return buildKnightGroup(toonMat, edgeMat);
        case 'tree': return buildTreeGroup(toonMat, edgeMat);
        default: {
            const g = new THREE.Group();
            addPart(g, new THREE.OctahedronGeometry(0.76, 0), toonMat, edgeMat);
            return g;
        }
    }
}

function createIconScene(shape, toonMat, edgeMat, phase) {
    const scene = new THREE.Scene();
    // Calibrated key/ambient ratio so MeshToonMaterial spans all 3 discrete bands
    // (ink-black shadow, mid-gray side planes, pure white key facets) instead of clipping white.
    const amb = new THREE.AmbientLight(0xffffff, 0.14);
    const key = new THREE.DirectionalLight(0xffffff, 1.35);
    key.position.set(-2.8, 2.4, 1.7);
    const rim = new THREE.DirectionalLight(0xffffff, 0.22);
    rim.position.set(2.4, -1.6, 0.8);
    scene.add(amb, key, rim);

    const group = buildRepoGroup(shape, toonMat, edgeMat);
    group.rotation.set(0.42, phase, 0);
    scene.add(group);
    return { scene, group };
}

function disposeGroup(group) {
    group.traverse(obj => {
        if (obj.geometry) obj.geometry.dispose();
    });
}

function isLightThemeActive() {
    const t = document.documentElement.getAttribute('data-theme');
    return t === 'egg' || t === 'pastel';
}

class Rail3DManager {
    constructor() {
        this.rail = null;
        this.canvas = null;
        this.renderer = null;
        this.camera = new THREE.PerspectiveCamera(34, 1, 0.1, 20);
        this.camera.position.set(0, 0, 3.75);
        this.gradientMap = null;
        this.toonMat = null;
        this.edgeMat = null;
        this.items = [];
        this.rafId = 0;
        this.lastTs = 0;
        this.webglFailed = false;
        this.resizeObserver = null;
        this.reducedMotion = window.matchMedia
            ? window.matchMedia('(prefers-reduced-motion: reduce)')
            : null;
        this._boundTick = (ts) => this._tick(ts);
        this._boundTheme = () => this._syncTheme();
        this._boundVis = () => this._syncRunning();
    }

    _ensureRenderer(rail) {
        if (this.webglFailed) return false;
        if (!this.canvas) {
            this.canvas = document.createElement('canvas');
            this.canvas.className = 'rail-3d-canvas';
            this.canvas.setAttribute('aria-hidden', 'true');
            this.canvas.addEventListener('webglcontextlost', (e) => {
                e.preventDefault();
                this._fallbackTo2D();
            });
        }
        if (this.canvas.parentElement !== rail) {
            rail.appendChild(this.canvas);
        }
        if (!this.renderer) {
            try {
                this.renderer = new THREE.WebGLRenderer({
                    canvas: this.canvas,
                    alpha: true,
                    antialias: true,
                    powerPreference: 'low-power',
                });
                this.renderer.setClearColor(0x000000, 0);
                this.renderer.outputColorSpace = THREE.SRGBColorSpace;
                this.gradientMap = createThreeToneGradient();
                this.toonMat = new THREE.MeshToonMaterial({
                    color: 0xffffff,
                    gradientMap: this.gradientMap,
                });
                this.edgeMat = new THREE.LineBasicMaterial({
                    color: isLightThemeActive() ? 0x0a0a0a : 0xffffff,
                });
                window.addEventListener('hn:theme', this._boundTheme);
                document.addEventListener('visibilitychange', this._boundVis);
            } catch {
                this._fallbackTo2D();
                return false;
            }
        }
        if (!this.resizeObserver && typeof ResizeObserver !== 'undefined') {
            this.resizeObserver = new ResizeObserver(() => this._renderFrame(0));
            this.resizeObserver.observe(rail);
        }
        return true;
    }

    _fallbackTo2D() {
        this.webglFailed = true;
        this._stopLoop();
        if (this.canvas) this.canvas.style.display = 'none';
        if (this.rail) this.rail.classList.add('rail-3d-fallback');
    }

    _syncTheme() {
        if (!this.edgeMat) return;
        this.edgeMat.color.setHex(isLightThemeActive() ? 0x0a0a0a : 0xffffff);
        this._renderFrame(0);
    }

    _clearItems() {
        for (const it of this.items) {
            disposeGroup(it.group);
        }
        this.items = [];
    }

    sync(rail) {
        if (!rail) return;
        this.rail = rail;
        const slots = Array.from(rail.querySelectorAll('.rail-icon-3d[data-app-id]'));
        if (!slots.length) {
            this.stop();
            return;
        }
        if (!this._ensureRenderer(rail)) {
            rail.classList.add('rail-3d-fallback');
            return;
        }
        rail.classList.remove('rail-3d-fallback');
        this.canvas.style.display = 'block';

        this._clearItems();
        slots.forEach((slot, idx) => {
            const shape = resolveRepoShape(slot.getAttribute('data-app-id'));
            const phase = 0.45 + idx * 0.55;
            const { scene, group } = createIconScene(shape, this.toonMat, this.edgeMat, phase);
            const item = { slot, link: slot.closest('.rail-item'), scene, group, hovered: false };
            if (item.link) {
                item.link.addEventListener('mouseenter', () => { item.hovered = true; });
                item.link.addEventListener('mouseleave', () => { item.hovered = false; });
                item.link.addEventListener('focus', () => { item.hovered = true; });
                item.link.addEventListener('blur', () => { item.hovered = false; });
            }
            this.items.push(item);
        });

        this._renderFrame(0);
        this._syncRunning();
    }

    _syncRunning() {
        const reduce = !!(this.reducedMotion && this.reducedMotion.matches);
        if (document.hidden || reduce || !this.items.length || this.webglFailed) {
            this._stopLoop();
            if (reduce && this.items.length && !this.webglFailed) this._renderFrame(0);
            return;
        }
        if (!this.rafId) {
            this.lastTs = performance.now();
            this.rafId = requestAnimationFrame(this._boundTick);
        }
    }

    _stopLoop() {
        if (this.rafId) {
            cancelAnimationFrame(this.rafId);
            this.rafId = 0;
        }
    }

    stop() {
        this._stopLoop();
        this._clearItems();
        if (this.canvas) this.canvas.style.display = 'none';
    }

    _tick(ts) {
        this.rafId = 0;
        const dt = Math.min(0.05, Math.max(0, (ts - this.lastTs) / 1000));
        this.lastTs = ts;
        // The rail sits beside every widget: a 60 fps WebGL redraw plus a
        // getBoundingClientRect per item per frame competes with video decode
        // and canvas work. Idle icons only need their slow spin, so redraw at
        // ~12 fps; a hovered item keeps the full frame rate for its fast spin.
        const hovered = this.items.some(it => it.hovered);
        this._idleAcc = (this._idleAcc || 0) + dt;
        if (hovered || this._idleAcc >= 1 / 12) {
            this._renderFrame(hovered ? dt : this._idleAcc);
            this._idleAcc = 0;
        }
        if (!document.hidden && this.items.length && !this.webglFailed) {
            this.rafId = requestAnimationFrame(this._boundTick);
        }
    }

    _renderFrame(dt) {
        if (!this.renderer || !this.rail || !this.items.length || this.webglFailed) return;
        const railRect = this.rail.getBoundingClientRect();
        const rw = Math.floor(railRect.width);
        const rh = Math.floor(railRect.height);
        if (rw < 4 || rh < 4) return;

        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        if (this.renderer.getPixelRatio() !== dpr) this.renderer.setPixelRatio(dpr);
        const sz = this.renderer.getSize(new THREE.Vector2());
        if (Math.round(sz.x) !== rw || Math.round(sz.y) !== rh) {
            this.renderer.setSize(rw, rh, false);
        }

        const listEl = this.rail.querySelector('.rail-list');
        const clipRect = listEl ? listEl.getBoundingClientRect() : railRect;

        this.renderer.setScissorTest(false);
        this.renderer.clear();
        this.renderer.setScissorTest(true);

        for (const it of this.items) {
            if (!it.slot.isConnected) continue;
            const r = it.slot.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) continue;
            if (r.bottom < clipRect.top || r.top > clipRect.bottom) continue;

            if (dt > 0) {
                const spinSpeed = it.hovered ? 2.25 : 0.55;
                it.group.rotation.y += spinSpeed * dt;
                const targetX = it.hovered ? 0.62 : 0.42;
                it.group.rotation.x += (targetX - it.group.rotation.x) * Math.min(1, dt * 8);
            }

            const left = Math.round(r.left - railRect.left);
            const bottom = Math.round(railRect.bottom - r.bottom);
            const w = Math.round(r.width);
            const h = Math.round(r.height);
            const clipTop = Math.max(r.top, clipRect.top);
            const clipBot = Math.min(r.bottom, clipRect.bottom);
            const scH = Math.max(0, Math.round(clipBot - clipTop));
            const scBot = Math.round(railRect.bottom - clipBot);
            if (scH < 2) continue;

            this.renderer.setViewport(left, bottom, w, h);
            this.renderer.setScissor(left, scBot, w, scH);
            this.renderer.render(it.scene, this.camera);
        }
        this.renderer.setScissorTest(false);
    }
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    const manager = new Rail3DManager();
    window.HN = window.HN || {};
    window.HN.Rail3D = {
        sync: (rail) => manager.sync(rail),
        stop: () => manager.stop(),
        resolveRepoShape,
        REPO_SHAPES,
    };
    window.dispatchEvent(new CustomEvent('hn:rail3d-ready'));
}
