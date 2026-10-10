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
  if (isSpeaking.value) return 'Assistant is responding'
  return open.value ? 'Close assistant menu' : 'Open assistant menu'
})
// 'responding' is the preferred name; 'subtitle' still works as an alias
const isSpeaking = computed(() => props.state === 'responding' || props.state === 'subtitle')
const showSubtitle = computed(() => isSpeaking.value && props.subtitle.trim().length > 0)

function onOrbClick() {
  clickT = 0
  blinkT = 0
  blinkDur = 0.42
  nextBlink = 2.5 + Math.random() * 3.5
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

// When a response starts or a new subtitle line appears, glance at the text, then back at the user
watch(() => props.state, (v) => {
  if (v === 'responding' || v === 'subtitle') { lookAtText = true; glanceT = 1.1 }
})
watch(() => props.subtitle, () => {
  if (isSpeaking.value) { lookAtText = true; glanceT = 1.0 }
})

/* ---------- face rendering ---------- */
const canvas = ref(null)
const S = 128          // canvas size in CSS px (leaves room for the halo)
const R = 36           // half-size of the face in CSS px
const C = S / 2

// Per-state targets. Everything eases toward these, so state changes feel fluid.
const TARGETS = {
  idle:      { scale: 1.0,  halo: 0.55, wide: 1.0,  brow: 0.0 },
  listening: { scale: 1.06, halo: 1.0,  wide: 1.1,  brow: 0.35 },
  subtitle:  { scale: 1.02, halo: 0.8,  wide: 1.04, brow: 0.1 },
  responding:{ scale: 1.02, halo: 0.8,  wide: 1.04, brow: 0.1 },
}

// Ring colour of the eyes per tone. The face eases between them when `tone` changes.
const RINGS = {
  neutral:  [60, 255, 150],
  warm:     [255, 170, 70],
  positive: [70, 235, 150],
  info:     [90, 175, 255],
}

let ctx, raf = 0, last = 0, t = 0, dpr = 1, reduced = false, mq
const cur = {
  scale: 1, halo: 0.55, wide: 1, brow: 0,
  ring: [...RINGS.neutral],
  gazeX: 0, gazeY: 0,
  browLift: 0, browTilt: 0,
  mouth: 0,
  blink: 0,
  grin: 0,
  press: 0,
  dilate: 1,    // pupil size multiplier
  tilt: 0,      // head rotation (radians)
  browUp: 0,    // extra lift on the right brow (curious look)
  eyeGlow: 0.9, // eye ring glow multiplier
  speech: 0,    // smoothed speech energy 0..1 (drives mouth, brows, glow, bob while responding)
  bob: 0,       // tiny vertical nod while talking
}

let nextBlink = 2.5, blinkT = -1
let blinkDur = 0.18
let nextGrin = 3, grinT = -1
let clickT = -1

// Responding: glance between the subtitle bubble (to the left) and the user
let lookAtText = true
let glanceT = 1.2

// Fallback speech envelope used when no audio `level` is supplied while responding.
// Syllable-rate flutter x word-level emphasis x phrase pauses.
function simulatedSpeech(now) {
  const syl = Math.abs(Math.sin(now / 118))
  const word = 0.55 + 0.45 * Math.sin(now / 290 + 2)
  const phrase = Math.sin(now / 1900) + 0.5 * Math.sin(now / 730)
  const gate = Math.min(1, Math.max(0.08, (phrase + 1.1) * 2))
  return syl * word * gate
}

// Sound ripples radiating from the face while listening
const ripples = []
let rippleCD = 0
const RIPPLE_LIFE = 1.4

// Pointer position in viewport px (null until the cursor moves)
const pointer = { x: null, y: null }

function onPointerMove(e) { pointer.x = e.clientX; pointer.y = e.clientY }
function onPointerLeave() { pointer.x = null; pointer.y = null }

function drawEye(ex, ey, er, ring, openness, glowMul) {
  const [rr, rg, rb] = ring
  const ringW = er * 0.3
  ctx.save()
  ctx.translate(ex, ey)
  ctx.scale(1, Math.max(openness, 0.06)) // blink squashes the eye vertically

  // Glowing ring (glow grows with voice level)
  ctx.shadowColor = `rgba(${rr},${rg},${rb},0.9)`
  ctx.shadowBlur = er * glowMul
  ctx.strokeStyle = `rgb(${rr},${rg},${rb})`
  ctx.lineWidth = ringW
  ctx.beginPath()
  ctx.arc(0, 0, er, 0, Math.PI * 2)
  ctx.stroke()
  ctx.shadowBlur = 0

  // Dark gap between ring and eye white
  ctx.fillStyle = '#05161a'
  ctx.beginPath()
  ctx.arc(0, 0, er - ringW / 2, 0, Math.PI * 2)
  ctx.fill()

  // Eye white
  const whiteR = er - ringW * 0.95
  ctx.fillStyle = '#f4fbff'
  ctx.beginPath()
  ctx.arc(0, 0, whiteR, 0, Math.PI * 2)
  ctx.fill()

  // Pupil follows the cursor (and dilates while listening)
  const travel = whiteR * 0.38
  const px = cur.gazeX * travel
  const py = cur.gazeY * travel
  const pr = whiteR * 0.62 * cur.dilate
  ctx.fillStyle = '#050607'
  ctx.beginPath()
  ctx.arc(px, py, pr, 0, Math.PI * 2)
  ctx.fill()

  // Catch-light
  ctx.fillStyle = 'rgba(255,255,255,0.9)'
  ctx.beginPath()
  ctx.arc(px - pr * 0.3, py - pr * 0.35, pr * 0.22, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()
}

function drawBrow(bx, by, w, tilt, curve) {
  // tilt > 0 raises the outer end; curve arches the brow
  ctx.beginPath()
  ctx.moveTo(bx - w, by + tilt)
  ctx.quadraticCurveTo(bx, by - curve, bx + w, by - tilt)
  ctx.stroke()
}

function draw(now) {
  const r0 = R * cur.scale
  const ring = cur.ring.map(Math.round)
  const [rr, rg, rb] = ring

  ctx.clearRect(0, 0, S, S)

  // Soft translucent white cloud: no clip, no rim, it just fades out.
  // Many stops with eased alpha so there's no visible "ring" where it ends.
  const fade = Math.min(r0 * 1.65, C - 1)
  const body = ctx.createRadialGradient(C, C - r0 * 0.05, 0, C, C, fade)
  body.addColorStop(0.00, 'rgba(255,255,255,0.80)')
  body.addColorStop(0.30, 'rgba(255,255,255,0.68)')
  body.addColorStop(0.50, 'rgba(250,253,255,0.46)')
  body.addColorStop(0.70, 'rgba(246,251,252,0.22)')
  body.addColorStop(0.85, 'rgba(244,250,251,0.07)')
  body.addColorStop(1.00, 'rgba(244,250,251,0)')
  ctx.fillStyle = body
  ctx.fillRect(0, 0, S, S)

  // Tint from the eye colour so tone / listening changes read clearly
  const glow = ctx.createRadialGradient(C, C, r0 * 0.4, C, C, fade)
  glow.addColorStop(0, `rgba(${rr},${rg},${rb},0)`)
  glow.addColorStop(0.6, `rgba(${rr},${rg},${rb},${0.18 * (cur.halo + cur.speech * 0.5)})`)
  glow.addColorStop(1, `rgba(${rr},${rg},${rb},0)`)
  ctx.fillStyle = glow
  ctx.fillRect(0, 0, S, S)

  // Sound ripples (drawn behind the face, unaffected by head tilt)
  for (const rp of ripples) {
    const p = rp.age / RIPPLE_LIFE
    const eased = 1 - Math.pow(1 - p, 2)
    ctx.strokeStyle = `rgba(${rr},${rg},${rb},${0.45 * rp.amp * (1 - p)})`
    ctx.lineWidth = 2.5 * (1 - p) + 0.5
    ctx.beginPath()
    ctx.arc(C, C, r0 * (0.85 + 0.75 * eased), 0, Math.PI * 2)
    ctx.stroke()
  }

  ctx.save()
  ctx.translate(C, C)
  ctx.rotate(cur.tilt)
  ctx.translate(0, cur.bob * r0)

  // Eyes
  const eyeX = r0 * 0.36
  const eyeY = -r0 * 0.06
  const er = r0 * 0.25 * cur.wide
  const openness = 1 - cur.blink
  drawEye(-eyeX, eyeY, er, ring, openness, cur.eyeGlow)
  drawEye(eyeX, eyeY, er, ring, openness, cur.eyeGlow)

  // Eyebrows
  const calm = reduced ? 0 : 1
  const wobbleL = Math.sin(now / 1900) * r0 * 0.03 * calm
  const wobbleR = Math.sin(now / 2300 + 1.3) * r0 * 0.03 * calm
  const baseY = eyeY - er - r0 * 0.2
  const lift = cur.browLift * r0 * 0.16 + cur.brow * r0 * 0.08 + cur.grin * r0 * 0.03 - cur.press * r0 * 0.1
  const browW = r0 * 0.2
  ctx.strokeStyle = '#0b2228'
  ctx.lineWidth = Math.max(2, r0 * 0.075)
  ctx.lineCap = 'round'
  drawBrow(-eyeX, baseY - lift + wobbleL, browW, cur.browTilt * r0 * 0.08 * -1 + wobbleL * 0.6, r0 * 0.07)
  drawBrow(eyeX, baseY - lift - cur.browUp * r0 * 0.05 + wobbleR, browW, cur.browTilt * r0 * 0.08 + wobbleR * 0.6, r0 * 0.07)

  // Mouth: living smile
  const mv = reduced ? 0 : 1
  const mouthY = r0 * 0.4 + Math.sin(now / 1500) * r0 * 0.012 * mv
  const m = cur.mouth
  // mouth narrows slightly as it opens (rounder "o" shapes while talking)
  const mw = r0 * (0.24 + 0.02 * Math.sin(now / 1700 + 0.5) * mv + cur.grin * 0.06) * (1 - Math.min(m, 0.6) * 0.3)
  const smile = r0 * (0.12 + 0.025 * Math.sin(now / 1100) * mv + cur.grin * 0.07)
  const cornerL = Math.sin(now / 1300) * r0 * 0.012 * mv
  const cornerR = Math.cos(now / 1700) * r0 * 0.012 * mv
  const sway = Math.sin(now / 2100) * r0 * 0.02 * mv
  const yL = mouthY - smile * 0.4 + cornerL
  const yR = mouthY - smile * 0.4 + cornerR

  ctx.fillStyle = '#0b2228'
  ctx.strokeStyle = '#0b2228'
  ctx.lineWidth = Math.max(2, r0 * 0.07)
  ctx.lineCap = 'round'
  ctx.beginPath()
  ctx.moveTo(-mw, yL)
  ctx.quadraticCurveTo(sway, (yL + yR) / 2 + smile * 2, mw, yR)
  if (m > 0.03) {
    ctx.quadraticCurveTo(sway, (yL + yR) / 2 + smile * 2 + m * r0 * 0.7, -mw, yL)
    ctx.fill()
  } else {
    ctx.stroke()
  }
  ctx.restore()
}

function frame(now) {
  raf = requestAnimationFrame(frame)
  const dt = Math.min((now - last) / 1000 || 0, 0.05)
  last = now
  t += dt

  const listening = props.state === 'listening'
  const speaking = props.state === 'responding' || props.state === 'subtitle'
  const level = Math.min(Math.max(props.level, 0), 1)

  // Speech energy while responding: real audio level if provided, otherwise simulated
  let speechRaw = 0
  if (speaking) speechRaw = level > 0.02 ? level : (reduced ? 0.3 : simulatedSpeech(now))
  cur.speech += (speechRaw - cur.speech) * (1 - Math.exp(-dt * 12))
  cur.bob = reduced ? 0 : cur.speech * Math.sin(now / 190) * 0.025

  // Sound ripples: spawn while listening (faster when louder), always age out
  if (listening && !reduced) {
    rippleCD -= dt
    if (rippleCD <= 0) {
      ripples.push({ age: 0, amp: 0.5 + level * 0.5 })
      rippleCD = 0.9 - level * 0.5
    }
  }
  for (const rp of ripples) rp.age += dt
  while (ripples.length && ripples[0].age > RIPPLE_LIFE) ripples.shift()

  const target = TARGETS[props.state] || TARGETS.idle
  const k = 1 - Math.exp(-dt * 4)
  for (const key of ['scale', 'halo', 'wide', 'brow']) cur[key] += (target[key] - cur[key]) * k

  // Listening extras: dilated pupils, head tilt, curious brow, voice-reactive eye glow
  cur.dilate += ((listening ? 1.18 : 1) - cur.dilate) * (1 - Math.exp(-dt * 6))
  let tiltTarget = 0
  if (listening) tiltTarget = 0.07 + (reduced ? 0 : Math.sin(t * 1.1) * 0.02) + level * 0.03
  else if (speaking && !reduced) tiltTarget = Math.sin(t * 2.2) * 0.03 * (0.3 + cur.speech)  // rocks with the words
  cur.tilt += (tiltTarget - cur.tilt) * (1 - Math.exp(-dt * 3))
  // Brow: curious while listening, emphasis lifts on stressed syllables while responding
  const browUpTarget = listening ? 1 : speaking ? cur.speech * 1.4 : 0
  cur.browUp += (browUpTarget - cur.browUp) * (1 - Math.exp(-dt * (speaking ? 8 : 4)))
  const glowTarget = 0.9 + (listening ? level * 1.6 : 0) + (speaking ? cur.speech * 1.3 : 0)
  cur.eyeGlow += (glowTarget - cur.eyeGlow) * (1 - Math.exp(-dt * 10))

  // Ring colour
  const pal = RINGS[props.tone] || RINGS.neutral
  const kc = 1 - Math.exp(-dt * 2.5)
  for (let j = 0; j < 3; j++) cur.ring[j] += (pal[j] - cur.ring[j]) * kc

  // Where is the cursor relative to the face?
  let gx = 0, gy = 0, bx = 0, by = 0
  if (listening) {
    // Lock eyes on the user
    gx = 0
    gy = 0.05
  } else if (speaking) {
    // Glance at the subtitle bubble (sits to the left of the face), then back at the user
    if (!reduced) {
      glanceT -= dt
      if (glanceT <= 0) {
        lookAtText = !lookAtText
        glanceT = lookAtText ? 1 + Math.random() * 1.2 : 1.8 + Math.random() * 2.2
      }
    }
    gx = lookAtText && !reduced && showSubtitle.value ? -0.75 : 0
    gy = lookAtText && !reduced && showSubtitle.value ? 0 : 0.05
  } else if (pointer.x !== null && canvas.value) {
    const rect = canvas.value.getBoundingClientRect()
    const dx = pointer.x - (rect.left + rect.width / 2)
    const dy = pointer.y - (rect.top + rect.height / 2)
    const dist = Math.hypot(dx, dy) || 1
    const pull = Math.min(dist / 140, 1)       // look fully sideways once the cursor is ~140px away
    gx = (dx / dist) * pull
    gy = (dy / dist) * pull
    bx = Math.max(-1, Math.min(1, dx / 400))   // brows react to a wider range, and more gently
    by = Math.max(-1, Math.min(1, dy / 400))
  } else if (!reduced) {
    // No cursor yet: glance around lazily
    gx = Math.sin(t * 0.6) * 0.35
    gy = Math.sin(t * 0.43 + 1) * 0.2
  }

  // Eyes snap quickly; eyebrows ease slowly
  const kg = 1 - Math.exp(-dt * 14)
  cur.gazeX += (gx - cur.gazeX) * kg
  cur.gazeY += (gy - cur.gazeY) * kg
  const kb = 1 - Math.exp(-dt * 1.1)
  cur.browLift += (-by - cur.browLift) * kb    // cursor above => brows rise
  cur.browTilt += (bx - cur.browTilt) * kb

  // Blinking (attentive faces blink less)
  if (!reduced) {
    nextBlink -= dt
    if (nextBlink <= 0 && blinkT < 0) {
      blinkT = 0
      blinkDur = 0.18
      nextBlink = (listening ? 4 : 2.5) + Math.random() * 3.5
    }
  }
  if (blinkT >= 0) {
    blinkT += dt
    const p = blinkT / blinkDur
    // Eased so the eye stays closed a moment at the bottom of the blink
    cur.blink = p < 1 ? Math.min(1, Math.sin(p * Math.PI) * (blinkDur > 0.3 ? 1.35 : 1)) : 0
    if (p >= 1) blinkT = -1
  }
  // Occasional bigger smile
  if (!reduced) {
    nextGrin -= dt
    if (nextGrin <= 0 && grinT < 0) { grinT = 0; nextGrin = 4 + Math.random() * 5 }
  }
  if (grinT >= 0) {
    grinT += dt
    const p = grinT / 1.4
    cur.grin = p < 1 ? Math.sin(p * Math.PI) : 0
    if (p >= 1) grinT = -1
  }
  if (clickT >= 0) {
    clickT += dt
    const p = clickT / 0.45
    cur.press = p < 1 ? Math.sin(p * Math.PI) : 0
    if (p >= 1) clickT = -1
  }

  // Mouth: nearly closed while listening (so it doesn't look like talking),
  // flutters gently while a subtitle is shown
  let mTarget = 0
  if (listening) mTarget = 0.02 + level * 0.12
  else if (speaking) mTarget = 0.04 + cur.speech * 0.5
  cur.mouth += (Math.max(mTarget, 0) - cur.mouth) * (1 - Math.exp(-dt * 12))

  // "Breathing" while idle; a gentle pulse while a subtitle is shown
  let pulse = 0
  if (props.state === 'idle') pulse = Math.sin(now / 1400) * 0.015
  if (speaking) pulse = (cur.speech - 0.3) * 0.025
  const saved = cur.scale
  cur.scale += pulse * (reduced ? 0.15 : 1)
  draw(now)
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
  document.addEventListener('pointermove', onPointerMove, { passive: true })
  document.documentElement.addEventListener('pointerleave', onPointerLeave)
  start()
})

onBeforeUnmount(() => {
  stop()
  document.removeEventListener('visibilitychange', onVisibility)
  document.removeEventListener('pointerdown', onDocPointer)
  document.removeEventListener('keydown', onDocKey)
  document.removeEventListener('pointermove', onPointerMove)
  document.documentElement.removeEventListener('pointerleave', onPointerLeave)
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

    <!-- Face -->
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
  right: max(28px, env(safe-area-inset-right));
  bottom: max(40px, env(safe-area-inset-bottom));
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

/* Face button — canvas is larger than the button so the halo can bleed out */
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
.orb:focus-visible { outline: 3px solid var(--accent); outline-offset: -14px; }

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