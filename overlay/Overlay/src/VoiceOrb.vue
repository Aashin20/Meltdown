<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'

const props = defineProps({
  state: { type: String, default: 'idle' },
  subtitle: { type: String, default: '' },
  level: { type: Number, default: 0 },
  caption: { type: String, default: '' },
  tone: { type: String, default: 'neutral' },
  priority: { type: String, default: 'none' },
  items: {
    type: Array,
    default: () => [
      { id: 'listen', label: 'Start Listening', icon: 'mic' },
      { id: 'info', label: 'Update Product Info', icon: 'keyboard' },
      { id: 'summary', label: 'Download Summary', icon: 'clock' },
      { id: 'settings', label: 'Settings', icon: 'gear' },
    ],
  },
})
const emit = defineEmits(['update:state', 'select'])

/* ---------- menu ---------- */
const open = ref(false)
const root = ref(null)
const orbBtn = ref(null)
const menuEl = ref(null)

const icons = {
  stop: ['M8.5 8.5h7v7h-7z'],
  mic: ['M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z', 'M6 11a6 6 0 0 0 12 0', 'M12 17v4'],
  keyboard: ['M3 7h18v10H3z', 'M7 11h.01M11 11h.01M15 11h.01M7 14h10'],
  clock: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z', 'M12 7v5l3 2'],
  gear: [
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
    'M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.3 3h-4l-.4 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2Z',
  ],
}

const orbLabel = computed(() => {
  if (props.state === 'listening') return 'Listening. Press to stop.'
  return open.value ? 'Close assistant menu' : 'Open assistant menu'
})
const showSubtitle = computed(() => props.state === 'subtitle' && props.subtitle.trim().length > 0)

function onOrbClick() {
  if (props.state === 'listening') {
    open.value = false
    emit('update:state', 'idle')
    return
  }
  open.value = !open.value
  if (open.value) nextTick(() => {})
}

function choose(item) {
  open.value = false
  emit('select', item.id)
  if (item.id === 'listen') emit('update:state', 'listening')
  orbBtn.value?.focus()
}

function onMenuKey(e) {
  const btns = [...menuEl.value.querySelectorAll('button')]
  const i = btns.indexOf(document.activeElement)
  if (e.key === 'ArrowUp') { e.preventDefault(); btns[(i - 1 + btns.length) % btns.length].focus() }
  if (e.key === 'ArrowDown') { e.preventDefault(); btns[(i + 1) % btns.length].focus() }
}

function onDocPointer(e) {
  if (open.value && root.value && !root.value.contains(e.target)) open.value = false
}
function onDocKey(e) {
  if (e.key === 'Escape' && open.value) { open.value = false; orbBtn.value?.focus() }
}

watch(open, (v) => {
  if (v) nextTick(() => menuEl.value?.querySelector('button')?.focus({ preventScroll: true }))
})

/* ---------- orb rendering ---------- */
const canvas = ref(null)
const S = 128          // canvas size in CSS px (leaves room for the halo)
const R = 36           // orb radius in CSS px
const C = S / 2

// Per-state targets. Everything eases toward these, so state changes feel fluid.
const TARGETS = {
  idle:      { speed: 0.55, amp: 0.07, glow: 0.65, scale: 1.0,  halo: 0.55 },
  listening: { speed: 1.7,  amp: 0.14, glow: 1.0,  scale: 1.06, halo: 1.0 },
  subtitle:  { speed: 1.05, amp: 0.10, glow: 0.85, scale: 1.02, halo: 0.8 },
}

const BLOBS = [
  { r: 0.62, orbit: 0.30, sp: 0.9,  seed: 0.0 },
  { r: 0.66, orbit: 0.34, sp: -0.7, seed: 2.1 },
  { r: 0.50, orbit: 0.38, sp: 1.1,  seed: 4.0 },
  { r: 0.60, orbit: 0.26, sp: -1.0, seed: 5.3 },
]

// One colour per blob. The orb eases between palettes when `tone` changes.
const PALETTES = {
  neutral:  [[255, 70, 190], [70, 190, 255], [90, 255, 200], [140, 90, 255]],
  warm:     [[255, 90, 70],  [255, 170, 60], [255, 70, 150], [200, 70, 255]],
  positive: [[60, 230, 150], [70, 200, 255], [170, 255, 120], [40, 180, 170]],
  info:     [[70, 170, 255], [100, 120, 255], [90, 230, 255], [170, 110, 255]],
}

let ctx, raf = 0, last = 0, t = 0, dpr = 1, reduced = false, mq
const cur = { ...TARGETS.idle, level: 0, colors: PALETTES.neutral.map((c) => [...c]) }

function blobPath(cx, cy, r, time, seed, amp) {
  const n = 56
  ctx.beginPath()
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * Math.PI * 2
    const w =
      Math.sin(a * 2 + time * 1.1 + seed) * 0.5 +
      Math.sin(a * 3 - time * 0.8 + seed * 1.7) * 0.35 +
      Math.sin(a * 5 + time * 1.6 + seed * 2.3) * 0.15
    const rr = r * (1 + amp * 2.2 * w)
    const x = cx + Math.cos(a) * rr
    const y = cy + Math.sin(a) * rr
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)
  }
  ctx.closePath()
}

function draw() {
  const amp = cur.amp + cur.level * 0.18
  const glow = cur.glow
  const r0 = R * cur.scale * (1 + cur.level * 0.05)

  ctx.clearRect(0, 0, S, S)

  // Soft halo outside the sphere
  const halo = ctx.createRadialGradient(C, C, r0 * 0.9, C, C, r0 * 1.75)
  halo.addColorStop(0, `rgba(110,150,255,${0.28 * cur.halo})`)
  halo.addColorStop(1, 'rgba(110,150,255,0)')
  ctx.fillStyle = halo
  ctx.fillRect(0, 0, S, S)

  ctx.save()
  ctx.beginPath()
  ctx.arc(C, C, r0, 0, Math.PI * 2)
  ctx.clip()

  // Dark glass body
  const body = ctx.createRadialGradient(C - r0 * 0.3, C - r0 * 0.3, 0, C, C, r0)
  body.addColorStop(0, '#1d1840')
  body.addColorStop(1, '#05060f')
  ctx.fillStyle = body
  ctx.fillRect(0, 0, S, S)

  // Wiggly colour blobs, blended additively
  ctx.globalCompositeOperation = 'lighter'
  for (const [i, b] of BLOBS.entries()) {
    const ox = Math.cos(t * b.sp + b.seed) * r0 * b.orbit
    const oy = Math.sin(t * b.sp * 0.8 + b.seed) * r0 * b.orbit
    const rad = r0 * b.r
    const g = ctx.createRadialGradient(C + ox, C + oy, 0, C + ox, C + oy, rad * 1.15)
    const [cr, cg, cb] = cur.colors[i]
    g.addColorStop(0, `rgba(${cr},${cg},${cb},${0.85 * glow})`)
    g.addColorStop(0.6, `rgba(${cr},${cg},${cb},${0.35 * glow})`)
    g.addColorStop(1, `rgba(${cr},${cg},${cb},0)`)
    ctx.fillStyle = g
    blobPath(C + ox, C + oy, rad, t, b.seed, amp)
    ctx.fill()
  }

  // Two thin translucent shards, like light catching glass
  for (let k = 0; k < 2; k++) {
    const a = t * (k ? -0.35 : 0.28) + k * 2.4
    const tip = r0 * (0.8 + Math.sin(t * 0.9 + k) * 0.08 * (1 + amp * 4))
    const w = r0 * 0.1
    ctx.save()
    ctx.translate(C, C)
    ctx.rotate(a)
    ctx.beginPath()
    ctx.moveTo(-r0 * 0.15, -w)
    ctx.lineTo(tip, 0)
    ctx.lineTo(-r0 * 0.15, w)
    ctx.closePath()
    const sg = ctx.createLinearGradient(0, 0, tip, 0)
    sg.addColorStop(0, `rgba(255,255,255,${0.5 * glow})`)
    sg.addColorStop(1, `rgba(255,120,220,${0.1 * glow})`)
    ctx.fillStyle = sg
    ctx.fill()
    ctx.restore()
  }

  // Bright core
  const cx = C + Math.sin(t * 0.7) * r0 * 0.08
  const cy = C + Math.cos(t * 0.6) * r0 * 0.08
  const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, r0 * (0.5 + cur.level * 0.15))
  core.addColorStop(0, `rgba(255,255,255,${0.95 * glow})`)
  core.addColorStop(0.4, `rgba(200,225,255,${0.4 * glow})`)
  core.addColorStop(1, 'rgba(200,225,255,0)')
  ctx.fillStyle = core
  ctx.fillRect(0, 0, S, S)

  // Glass edge: darkening toward the rim
  ctx.globalCompositeOperation = 'source-over'
  const rim = ctx.createRadialGradient(C, C, r0 * 0.6, C, C, r0)
  rim.addColorStop(0, 'rgba(0,0,0,0)')
  rim.addColorStop(1, 'rgba(2,4,14,0.65)')
  ctx.fillStyle = rim
  ctx.fillRect(0, 0, S, S)

  // Specular highlight
  const spec = ctx.createRadialGradient(C - r0 * 0.38, C - r0 * 0.5, 0, C - r0 * 0.38, C - r0 * 0.5, r0 * 0.5)
  spec.addColorStop(0, 'rgba(255,255,255,0.28)')
  spec.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = spec
  ctx.fillRect(0, 0, S, S)
  ctx.restore()

  // Hairline rim
  const edge = ctx.createLinearGradient(C - r0, C - r0, C + r0, C + r0)
  edge.addColorStop(0, 'rgba(255,255,255,0.45)')
  edge.addColorStop(0.5, 'rgba(255,255,255,0.05)')
  edge.addColorStop(1, 'rgba(120,220,255,0.35)')
  ctx.strokeStyle = edge
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.arc(C, C, r0 - 0.5, 0, Math.PI * 2)
  ctx.stroke()
}

function frame(now) {
  raf = requestAnimationFrame(frame)
  const dt = Math.min((now - last) / 1000 || 0, 0.05)
  last = now

  const target = TARGETS[props.state] || TARGETS.idle
  const k = 1 - Math.exp(-dt * 4)
  const boost = props.state === 'subtitle' ? { high: 0.05, medium: 0.025 }[props.priority] || 0 : 0
  for (const key of ['speed', 'amp', 'glow', 'scale', 'halo']) {
    cur[key] += ((key === 'amp' ? target.amp + boost : target[key]) - cur[key]) * k
  }
  const pal = PALETTES[props.tone] || PALETTES.neutral
  const kc = 1 - Math.exp(-dt * 2.5)
  cur.colors.forEach((c, i) => { for (let j = 0; j < 3; j++) c[j] += (pal[i][j] - c[j]) * kc })

  const lvlTarget = props.state === 'listening' ? Math.min(Math.max(props.level, 0), 1) : 0
  cur.level += (lvlTarget - cur.level) * (1 - Math.exp(-dt * 12))

  // "Breathing" while idle; a gentle pulse while a subtitle is shown
  let pulse = 0
  if (props.state === 'idle') pulse = Math.sin(now / 1400) * 0.015
  if (props.state === 'subtitle') pulse = Math.sin(now / 380) * 0.02
  const calm = reduced ? 0.15 : 1
  t += dt * cur.speed * calm
  const saved = cur.scale
  cur.scale += pulse * calm
  draw()
  cur.scale = saved
}

function start() {
  if (raf) return
  last = performance.now()
  raf = requestAnimationFrame(frame)
}
function stop() { cancelAnimationFrame(raf); raf = 0 }
function onVisibility() { document.hidden ? stop() : start() }

onMounted(() => {
  dpr = Math.min(window.devicePixelRatio || 1, 3)
  const el = canvas.value
  el.width = S * dpr
  el.height = S * dpr
  ctx = el.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  mq = window.matchMedia('(prefers-reduced-motion: reduce)')
  reduced = mq.matches
  mq.addEventListener?.('change', (e) => (reduced = e.matches))

  document.addEventListener('visibilitychange', onVisibility)
  document.addEventListener('pointerdown', onDocPointer)
  document.addEventListener('keydown', onDocKey)
  start()
})

onBeforeUnmount(() => {
  stop()
  document.removeEventListener('visibilitychange', onVisibility)
  document.removeEventListener('pointerdown', onDocPointer)
  document.removeEventListener('keydown', onDocKey)
})
</script>

<template>
  <div ref="root" class="voice-orb" :data-state="state">
    <!-- Subtitle -->
    <Transition name="subtitle">
      <p v-if="showSubtitle" class="subtitle" role="status" aria-live="polite">
        <Transition name="text" mode="out-in">
          <span :key="subtitle" class="subtitle-body">
            <small v-if="caption" class="caption">{{ caption }}</small>
            <span class="subtitle-text">{{ subtitle }}</span>
          </span>
        </Transition>
      </p>
    </Transition>

    <!-- Menu -->
    <Transition name="menu">
      <ul v-if="open" ref="menuEl" class="menu material" role="menu" aria-label="Assistant" @keydown="onMenuKey">
        <li v-for="(item, i) in items" :key="item.id" role="none" :style="{ '--i': i }">
          <button class="menu-item" role="menuitem" type="button" @click="choose(item)">
            <svg class="icon" viewBox="0 0 24 24" aria-hidden="true">
              <path v-for="d in icons[item.icon] || []" :key="d" :d="d" />
            </svg>
            <span>{{ item.label }}</span>
          </button>
        </li>
      </ul>
    </Transition>

    <!-- Orb -->
    <button
      ref="orbBtn"
      class="orb"
      type="button"
      :aria-label="orbLabel"
      :aria-expanded="open"
      aria-haspopup="menu"
      @click="onOrbClick"
    >
      <canvas ref="canvas" :style="{ width: '128px', height: '128px' }" aria-hidden="true" />
    </button>
  </div>
</template>

<style scoped>
.voice-orb {
  --label: #1c1c1e;
  --label-secondary: rgba(60, 60, 67, 0.6);
  --material: rgba(246, 246, 248, 0.72);
  --material-border: rgba(0, 0, 0, 0.08);
  --hover: rgba(120, 120, 128, 0.14);
  --accent: #0a84ff;
  --spring: cubic-bezier(0.34, 1.4, 0.64, 1);

  /* Subtitle surface: quieter and lighter than the menu */
  --subtitle-bg: rgba(255, 255, 255, 0.78);
  --subtitle-ring: rgba(0, 0, 0, 0.06);
  --subtitle-shadow: 0 12px 40px rgba(0, 0, 0, 0.12);

  position: fixed;
  right: max(20px, env(safe-area-inset-right));
  bottom: max(20px, env(safe-area-inset-bottom));
  width: 72px;
  height: 72px;
  z-index: 1000;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', 'Segoe UI', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}

@media (prefers-color-scheme: dark) {
  .voice-orb {
    --label: #f5f5f7;
    --label-secondary: rgba(235, 235, 245, 0.6);
    --material: rgba(40, 40, 44, 0.66);
    --material-border: rgba(255, 255, 255, 0.12);
    --hover: rgba(235, 235, 245, 0.12);

    --subtitle-bg: rgba(28, 28, 30, 0.72);
    --subtitle-ring: rgba(255, 255, 255, 0.08);
    --subtitle-shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
  }
}

/* Orb button — canvas is larger than the button so the halo can bleed out */
.orb {
  position: absolute;
  inset: 0;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: none;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: transform 0.35s var(--spring);
}
.orb canvas {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  pointer-events: none;
}
.orb:active { transform: scale(0.92); }
.orb:focus-visible { outline: 3px solid var(--accent); outline-offset: 4px; }

/* Shared translucent material (menu) */
.material {
  background: var(--material);
  -webkit-backdrop-filter: saturate(180%) blur(24px);
  backdrop-filter: saturate(180%) blur(24px);
  border: 0.5px solid var(--material-border);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
  color: var(--label);
}

/* Menu */
.menu {
  position: absolute;
  right: 0;
  bottom: calc(100% + 14px);
  margin: 0;
  padding: 6px;
  list-style: none;
  min-width: 220px;
  border-radius: 20px;
  transform-origin: bottom right;
}
.menu li { animation: item-in 0.4s var(--spring) both; animation-delay: calc(var(--i) * 28ms + 60ms); }
.menu-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 44px;
  padding: 0 14px;
  border: 0;
  border-radius: 14px;
  background: none;
  color: inherit;
  font: inherit;
  font-size: 17px;
  letter-spacing: -0.02em;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease;
}
.menu-item:hover { background: var(--hover); }
.menu-item:active { background: var(--hover); transform: scale(0.98); }
.menu-item:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.icon {
  width: 22px;
  height: 22px;
  flex: none;
  fill: none;
  stroke: var(--accent);
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}

/* Subtitle — larger, borderless, one soft surface, type does the work */
.subtitle {
  position: absolute;
  right: calc(100% + 8px);
  top: 50%;
  margin: 0;
  width: max-content;
  min-width: 160px;
  max-width: min(440px, calc(100vw - 150px));
  padding: 18px 24px;
  border: 0;
  border-radius: 28px;
  background: var(--subtitle-bg);
  -webkit-backdrop-filter: saturate(180%) blur(30px);
  backdrop-filter: saturate(180%) blur(30px);
  /* 0.5px ring instead of a border keeps the edge crisp but invisible */
  box-shadow: 0 0 0 0.5px var(--subtitle-ring), var(--subtitle-shadow);
  color: var(--label);
  transform: translateY(-50%);
  transform-origin: right center;
}

.subtitle-body { display: block; }
.subtitle-text {
  display: block;
  font-size: 22px;
  font-weight: 500;
  line-height: 1.3;
  letter-spacing: -0.022em;
  text-wrap: pretty;
}
.caption {
  display: block;
  margin-bottom: 4px;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.3;
  letter-spacing: -0.01em;
  color: var(--label-secondary);
}

/* Transitions */
.menu-enter-active { transition: opacity 0.2s ease, transform 0.45s var(--spring); }
.menu-leave-active { transition: opacity 0.15s ease, transform 0.2s ease; }
.menu-enter-from, .menu-leave-to { opacity: 0; transform: scale(0.6) translateY(12px); }

.subtitle-enter-active { transition: opacity 0.25s ease, transform 0.45s var(--spring); }
.subtitle-leave-active { transition: opacity 0.25s ease, transform 0.25s ease; }
.subtitle-enter-from, .subtitle-leave-to { opacity: 0; transform: translateY(-50%) scale(0.9); }

.text-enter-active, .text-leave-active { transition: opacity 0.18s ease; }
.text-enter-from, .text-leave-to { opacity: 0; }

@keyframes item-in {
  from { opacity: 0; transform: translateY(8px) scale(0.96); }
  to { opacity: 1; transform: none; }
}

@media (max-width: 480px) {
  .subtitle { padding: 16px 20px; border-radius: 24px; }
  .subtitle-text { font-size: 19px; }
}

@media (prefers-reduced-transparency: reduce) {
  .voice-orb { --material: Canvas; --subtitle-bg: Canvas; }
  .material, .subtitle { -webkit-backdrop-filter: none; backdrop-filter: none; }
}
@media (prefers-reduced-motion: reduce) {
  .menu li { animation: none; }
  .menu-enter-active, .menu-leave-active,
  .subtitle-enter-active, .subtitle-leave-active { transition: opacity 0.15s ease; }
  .menu-enter-from, .menu-leave-to { transform: none; }
  .subtitle-enter-from, .subtitle-leave-to { transform: translateY(-50%); }
}
</style>