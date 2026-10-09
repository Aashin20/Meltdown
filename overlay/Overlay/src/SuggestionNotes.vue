<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'

const props = defineProps({
  pending: { type: [Object, Array], default: () => ({}) },
  completed: { type: [Object, Array], default: () => ({}) },
  resetKey: { type: [Number, String], default: 0 }, // change it to clear the board
  max: { type: Number, default: 12 },
  // Off by default: the backend is the source of truth for completion
  // (resolved_suggestion_ids -> completed_suggestions).
  inferCompleted: { type: Boolean, default: false },
  // how long a finished note stays visible (strike + tick animation) before removal
  removeDelay: { type: Number, default: 1300 },
})

const notes = ref([]) // newest first
const collapsed = ref(false)

// ids that were completed/removed; never re-add them even if the backend still lists them
const dismissed = new Set()
const timers = new Map()

// same event_type -> tone map as the parent, so notes echo the orb's eye colour
const TONES = {
  objection: 'warm',
  buying_signal: 'positive',
  next_step: 'positive',
  customer_question: 'info',
  customer_need: 'info',
  missed_opportunity: 'info',
  continue_listening: 'neutral',
}

const openCount = computed(() => notes.value.filter((n) => !n.done).length)
const summary = computed(() => `${openCount.value} open`)

function entries(src) {
  if (!src) return []
  if (Array.isArray(src)) {
    return src.map((v, i) => [v && typeof v === 'object' ? (v.id ?? v.suggestion ?? i) : v, v])
  }
  return Object.entries(src)
}

function shape(id, v) {
  if (typeof v === 'string') return { id: String(id), text: v, caption: '', tone: 'neutral' }
  const o = v || {}
  return {
    id: String(id),
    text: String(o.suggestion ?? o.text ?? o.title ?? ''),
    caption: String(o.customer_intent ?? o.caption ?? o.topic ?? ''),
    tone: o.tone || TONES[o.event_type] || 'neutral',
  }
}

// small stable tilt per note (-1.4° … 1.4°) so the stack feels hand-placed
function tiltFor(id) {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0
  return ((Math.abs(h) % 29) / 10 - 1.4).toFixed(2)
}

// mark a note done (plays the strike + tick), then remove it after a short delay
function markDone(id) {
  dismissed.add(id)
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.done = true
  if (timers.has(id)) return
  timers.set(
    id,
    setTimeout(() => {
      timers.delete(id)
      const i = notes.value.findIndex((n) => n.id === id)
      if (i >= 0) notes.value.splice(i, 1)
    }, props.removeDelay),
  )
}

function onNoteClick(n) {
  if (!n.done) markDone(n.id)
}

function reconcile() {
  const list = notes.value
  const known = new Set(list.map((n) => n.id))

  // backend-completed ids: finish them if visible, otherwise just never show them
  for (const [id] of entries(props.completed)) {
    const key = String(id)
    if (known.has(key)) markDone(key)
    else dismissed.add(key)
  }

  // new pending notes land on top (the last one in the batch ends up highest)
  for (const [id, v] of entries(props.pending)) {
    const n = shape(id, v)
    if (!n.text || dismissed.has(n.id)) continue
    if (known.has(n.id)) {
      const existing = list.find((item) => item.id === n.id)
      if (existing && !existing.done &&
          (existing.text !== n.text || existing.caption !== n.caption || existing.tone !== n.tone)) {
        Object.assign(existing, n, { done: false, tilt: existing.tilt })
      }
      continue
    }
    list.unshift({ ...n, done: false, tilt: tiltFor(n.id) })
    known.add(n.id)
  }

  // optional guesswork: only when explicitly enabled
  if (props.inferCompleted) {
    const live = new Set(entries(props.pending).map(([id]) => String(id)))
    for (const n of [...list]) if (!n.done && !live.has(n.id)) markDone(n.id)
  }

  // keep the board short: drop the oldest finished note first, never an open one
  while (list.length > props.max) {
    const i = list.findLastIndex((n) => n.done)
    if (i < 0) break
    list.splice(i, 1)
  }
}

function clearAll() {
  for (const t of timers.values()) clearTimeout(t)
  timers.clear()
  dismissed.clear()
  notes.value = []
}

watch(() => [props.pending, props.completed], reconcile, { deep: true, immediate: true })
watch(() => props.resetKey, clearAll)
onBeforeUnmount(() => {
  for (const t of timers.values()) clearTimeout(t)
  timers.clear()
})
</script>

<template>
  <Transition name="board">
    <aside v-if="notes.length" class="notes" :class="{ collapsed }" aria-label="Talking points">
      <header class="head">
        <span class="title">Talking points</span>
        <span class="count">{{ summary }}</span>
        <button
          class="fold"
          type="button"
          :aria-expanded="!collapsed"
          :aria-label="collapsed ? 'Show talking points' : 'Hide talking points'"
          @click="collapsed = !collapsed"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9.5l6 6 6-6" /></svg>
        </button>
      </header>

      <div class="fold-wrap">
        <div class="fold-inner">
          <TransitionGroup tag="ul" name="note" class="list" aria-live="polite">
            <li
              v-for="n in notes"
              :key="n.id"
              class="slot"
              :data-tone="n.tone"
              :data-done="n.done"
              :style="{ '--tilt': n.tilt + 'deg' }"
            >
              <div
                class="note"
                role="button"
                tabindex="0"
                :aria-label="n.done ? 'Completed' : 'Mark as done: ' + n.text"
                @click="onNoteClick(n)"
                @keydown.enter.prevent="onNoteClick(n)"
                @keydown.space.prevent="onNoteClick(n)"
              >
                <span class="tape" aria-hidden="true"></span>

                <span class="status" aria-hidden="true">
                  <i class="dot"></i>
                  <svg class="tick" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
                </span>

                <small v-if="n.caption" class="cap">{{ n.caption }}</small>
                <p class="txt"><span class="strike">{{ n.text }}</span><span class="sr"> {{ n.done ? '(done)' : '' }}</span></p>
              </div>
            </li>
          </TransitionGroup>
        </div>
      </div>
    </aside>
  </Transition>
</template>

<style scoped>
.notes {
  --label: #1c1c1e;
  --label-secondary: rgba(60, 60, 67, 0.6);
  --material: rgba(246, 246, 248, 0.72);
  --material-border: rgba(0, 0, 0, 0.08);
  --hover: rgba(120, 120, 128, 0.14);
  --spring: cubic-bezier(0.34, 1.4, 0.64, 1);

  position: fixed;
  top: max(20px, env(safe-area-inset-top));
  right: max(20px, env(safe-area-inset-right));
  z-index: 5; /* under the product-info sheet, well under the orb */
  width: min(280px, calc(100vw - 40px));
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', 'Segoe UI', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  color: var(--label);
}

@media (prefers-color-scheme: dark) {
  .notes {
    --label: #f5f5f7;
    --label-secondary: rgba(235, 235, 245, 0.6);
    --material: rgba(40, 40, 44, 0.66);
    --material-border: rgba(255, 255, 255, 0.12);
    --hover: rgba(235, 235, 245, 0.12);
  }
}

/* tone colours = the orb's eye ring colours */
.slot[data-tone='neutral'],
.slot:not([data-tone]) { --c: 60 255 150; }
.slot[data-tone='warm'] { --c: 255 170 70; }
.slot[data-tone='positive'] { --c: 70 235 150; }
.slot[data-tone='info'] { --c: 90 175 255; }

/* ---------- header pill ---------- */
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  max-width: 100%;
  margin: 0 0 10px auto;
  padding: 6px 6px 6px 14px;
  border-radius: 999px;
  background: var(--material);
  -webkit-backdrop-filter: saturate(180%) blur(24px);
  backdrop-filter: saturate(180%) blur(24px);
  box-shadow: 0 0 0 0.5px var(--material-border), 0 8px 24px rgba(0, 0, 0, 0.14);
}
.title { font-size: 13px; font-weight: 600; letter-spacing: -0.01em; }
.count { font-size: 12px; color: var(--label-secondary); white-space: nowrap; }
.fold {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  color: var(--label-secondary);
  background: none;
  border: 0;
  border-radius: 50%;
  cursor: pointer;
  transition: background 0.15s ease;
}
.fold:hover { background: var(--hover); }
.fold:focus-visible { outline: 2px solid #0a84ff; outline-offset: 1px; }
.fold svg {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform 0.4s var(--spring);
}
.collapsed .fold svg { transform: rotate(180deg); }

/* smooth collapse without measuring heights */
.fold-wrap {
  display: grid;
  grid-template-rows: 1fr;
  transition: grid-template-rows 0.45s var(--spring), opacity 0.25s ease;
}
.collapsed .fold-wrap { grid-template-rows: 0fr; opacity: 0; }
.fold-inner {
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  max-height: calc(100dvh - 250px); /* never run into the orb */
  padding: 4px 6px 4px 6px;
  margin: -4px -6px;
  scrollbar-width: none;
}
.fold-inner::-webkit-scrollbar { display: none; }

.list { position: relative; margin: 0; padding: 0; list-style: none; }
.slot { padding-bottom: 12px; }

/* ---------- the note ---------- */
.note {
  position: relative;
  padding: 16px 40px 14px 16px;
  border-radius: 16px;
  background:
    linear-gradient(145deg, rgb(var(--c) / 0.16), rgb(var(--c) / 0.03) 70%),
    var(--material);
  -webkit-backdrop-filter: saturate(180%) blur(24px);
  backdrop-filter: saturate(180%) blur(24px);
  box-shadow: 0 0 0 0.5px var(--material-border), 0 10px 28px rgba(0, 0, 0, 0.16);
  transform: rotate(var(--tilt, 0deg));
  transform-origin: 50% 0;
  cursor: pointer;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
  transition: transform 0.45s var(--spring), opacity 0.5s ease 0.2s, box-shadow 0.5s ease 0.2s;
}
.note:hover { transform: rotate(0deg) scale(1.02); opacity: 1; }
.note:active { transform: rotate(0deg) scale(0.98); }
.note:focus-visible { outline: 2px solid #0a84ff; outline-offset: 2px; }

/* a finished note is just waiting to leave; ignore further clicks */
.slot[data-done='true'] .note { pointer-events: none; cursor: default; }

/* a strip of tape holding the note up */
.tape {
  position: absolute;
  top: -5px;
  left: 50%;
  width: 44px;
  height: 11px;
  margin-left: -22px;
  border-radius: 3px;
  background: rgb(var(--c) / 0.32);
  box-shadow: 0 0 0 0.5px rgb(var(--c) / 0.25);
  transform: rotate(-2.5deg);
  transition: opacity 0.4s ease 0.2s;
}

.cap {
  display: block;
  margin-bottom: 3px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.3;
  letter-spacing: -0.005em;
  color: var(--label-secondary);
}
.txt {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
  line-height: 1.35;
  letter-spacing: -0.015em;
  text-wrap: pretty;
  transition: color 0.5s ease 0.2s;
}

/* strike line draws from left to right when the topic is done */
.strike {
  background: linear-gradient(rgb(var(--c)), rgb(var(--c))) no-repeat 0 60% / 0% 2px;
  -webkit-box-decoration-break: clone;
  box-decoration-break: clone;
  transition: background-size 0.6s cubic-bezier(0.65, 0, 0.35, 1) 0.25s;
}
.slot[data-done='true'] .strike { background-size: 100% 2px; }
.slot[data-done='true'] .txt { color: var(--label-secondary); }
.slot[data-done='true'] .note {
  opacity: 0.6;
  box-shadow: 0 0 0 0.5px var(--material-border), 0 4px 12px rgba(0, 0, 0, 0.08);
}
.slot[data-done='true'] .tape { opacity: 0.4; }

/* status: pulsing dot while open, drawn tick when done */
.status { position: absolute; top: 14px; right: 14px; width: 18px; height: 18px; }
.dot {
  position: absolute;
  inset: 5px;
  border-radius: 50%;
  background: rgb(var(--c));
  box-shadow: 0 0 8px rgb(var(--c) / 0.8);
  animation: pulse 2s ease-in-out infinite;
  transition: transform 0.3s ease, opacity 0.3s ease;
}
.slot[data-done='true'] .dot { transform: scale(0); opacity: 0; animation: none; }
.tick {
  position: absolute;
  inset: 0;
  width: 18px;
  height: 18px;
  fill: none;
  stroke: rgb(var(--c));
  stroke-width: 2.4;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-dasharray: 24;
  stroke-dashoffset: 24;
  transform: scale(0.6);
  transition: stroke-dashoffset 0.4s ease 0.55s, transform 0.5s var(--spring) 0.55s;
}
.slot[data-done='true'] .tick { stroke-dashoffset: 0; transform: none; }

@keyframes pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(0.65); opacity: 0.55; }
}

.sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

/* ---------- transitions ---------- */
.board-enter-active { transition: opacity 0.25s ease, transform 0.5s var(--spring); }
.board-leave-active { transition: opacity 0.2s ease, transform 0.25s ease; }
.board-enter-from, .board-leave-to { opacity: 0; transform: translateX(24px) scale(0.96); }

/* new note drops in from above; the rest slide down to make room */
.note-enter-active { transition: opacity 0.25s ease, transform 0.6s var(--spring); }
.note-enter-from { opacity: 0; transform: translateY(-28px) scale(0.9); }
.note-move { transition: transform 0.55s var(--spring); }
.note-leave-active {
  position: absolute;
  left: 0;
  right: 0;
  transition: opacity 0.25s ease, transform 0.35s ease;
}
.note-leave-to { opacity: 0; transform: translateX(28px) scale(0.92); }

@media (max-width: 480px) {
  .notes { width: min(230px, calc(100vw - 40px)); }
  .txt { font-size: 14px; }
}

@media (prefers-reduced-transparency: reduce) {
  .notes { --material: Canvas; }
  .head, .note { -webkit-backdrop-filter: none; backdrop-filter: none; }
}
@media (prefers-reduced-motion: reduce) {
  .dot { animation: none; }
  .note, .note:hover, .note:active { transform: none; }
  .note-enter-active, .note-move, .board-enter-active, .fold-wrap { transition: opacity 0.2s ease; }
  .note-enter-from, .board-enter-from, .board-leave-to, .note-leave-to { transform: none; }
  .strike { transition-duration: 0.01s; transition-delay: 0s; }
}
</style>