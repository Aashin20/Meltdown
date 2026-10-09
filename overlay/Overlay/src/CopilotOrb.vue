<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import VoiceOrb from './VoiceOrb.vue'

const props = defineProps({
  url: { type: String, default: 'ws://localhost:8000/ws/elevenlabs' },
  options: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['response', 'select'])

const state = ref('idle')
const active = ref(false)
const subtitle = ref('')
const caption = ref('')
const tone = ref('neutral')
const priority = ref('none')

// product info form
const productInfoOpen = ref(false)
const productInfoLoading = ref(false)
const productInfoSaving = ref(false)
const productInfoError = ref('')
const productInfoStatus = ref('')
const form = ref(null)
const tab = ref('overview')

let ws = null
let hideTimer = 0
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// event_type → orb colour
const TONES = {
  objection: 'warm',
  buying_signal: 'positive',
  next_step: 'positive',
  customer_question: 'info',
  customer_need: 'info',
  missed_opportunity: 'info',
  continue_listening: 'neutral',
}

const items = computed(() => [
  active.value
    ? { id: 'stop', label: 'Stop listening', icon: 'stop' }
    : { id: 'start', label: 'Start Listening', icon: 'mic' },
  { id: 'pdf', label: 'Download Summary', icon: 'clock' },
  { id: 'settings', label: 'Update Product Info', icon: 'gear' },
])

/* ---------- display ---------- */
function settle() {
  state.value = active.value ? 'listening' : 'idle'
  tone.value = 'neutral'
  priority.value = 'none'
}

function show(text, cap, nextTone, nextPriority, ms) {
  clearTimeout(hideTimer)
  subtitle.value = text
  caption.value = cap
  tone.value = nextTone
  priority.value = nextPriority
  state.value = 'subtitle'
  hideTimer = setTimeout(settle, ms)
}

function handleResponse(r) {
  if (!active.value || !r.suggestion_needed || !r.suggestion) return
  if (state.value === 'subtitle' && subtitle.value === r.suggestion) return
  const words = r.suggestion.trim().split(/\s+/).length
  const ms = Math.min(Math.max(2500 + words * 400, 4500), 12000)
  show(r.suggestion, r.customer_intent || '', TONES[r.event_type] || 'neutral', r.priority || 'none', ms)
}

/* ---------- socket ---------- */
// The backend's response envelope isn't known here, so accept the copilot JSON
// either bare or nested under data / payload / result (object or JSON string).
function extractResponse(msg) {
  let p = msg.data ?? msg.payload ?? msg.result ?? msg
  if (typeof p === 'string') {
    try { p = JSON.parse(p) } catch { return null }
  }
  return p && typeof p.suggestion_needed === 'boolean' ? p : null
}

function onMessage(ev) {
  let msg
  try { msg = JSON.parse(ev.data) } catch { return }

  if (msg.type === 'ready') {
    ev.target.send(JSON.stringify({ type: 'start', options: props.options }))
    return
  }
  if (msg.type === 'status') {
    if (msg.status === 'stopped' && active.value) {
      active.value = false
      clearTimeout(hideTimer)
      settle()
    }
    return
  }
  if (msg.type === 'error') {
    fail('Something went wrong', msg.message)
    return
  }

  const r = extractResponse(msg)
  if (!r) return
  emit('response', r)
  handleResponse(r)
}

function releaseSocket(sendStop) {
  const s = ws
  ws = null
  if (!s) return
  s.onmessage = s.onerror = s.onclose = null
  if (sendStop && s.readyState === WebSocket.OPEN) s.send(JSON.stringify({ type: 'stop' }))
  setTimeout(() => s.close(), sendStop ? 600 : 0) // let the stop message flush
}

function fail(cap, text) {
  active.value = false
  releaseSocket(false)
  show(text || cap, text ? cap : '', 'neutral', 'none', 6000)
}

function startSession() {
  if (ws) return
  active.value = true
  clearTimeout(hideTimer)
  settle()
  try {
    ws = new WebSocket(props.url)
  } catch (error) {
    fail('Can’t reach the assistant', error instanceof Error ? error.message : 'Invalid WebSocket URL.')
    return
  }
  ws.onmessage = onMessage
  ws.onerror = () => fail('Can’t reach the assistant', 'Check that the server is running, then try again.')
  ws.onclose = () => { if (active.value) fail('Connection lost', 'Press Start Listening to reconnect.') }
}

function stopSession() {
  active.value = false
  clearTimeout(hideTimer)
  releaseSocket(true)
  settle()
}

/* ---------- product info form ---------- */
const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'features', label: 'Features' },
  { id: 'pricing', label: 'Pricing' },
  { id: 'integrations', label: 'Integrations' },
  { id: 'rollout', label: 'Rollout' },
  { id: 'faq', label: 'FAQ' },
  { id: 'more', label: 'More' },
]

// string-list fields, grouped by the tab they appear in
const LISTS = {
  overview: [
    { key: 'target_customers', label: 'Target customers', add: 'Add customer type' },
    { key: 'use_cases', label: 'Use cases', add: 'Add use case' },
  ],
  more: [
    { key: 'security', label: 'Security', add: 'Add item' },
    { key: 'limitations', label: 'Limitations', add: 'Add limitation' },
  ],
}

function normalize(data) {
  const d = { ...data }
  d.name ??= ''
  d.description ??= ''
  d.target_customers ??= []
  d.features ??= []
  d.use_cases ??= []
  d.pricing = { currency: 'USD', plans: [], discount_policy: '', verified: false, ...(d.pricing || {}) }
  d.pricing.plans ??= []
  d.integrations ??= []
  d.implementation = { process: '', timeline: '', verified: false, ...(d.implementation || {}) }
  d.security ??= []
  d.limitations ??= []
  d.faq ??= []
  d.competitors ??= []
  d.evidence ??= []
  d.last_verified ??= ''
  return d
}

// Competitors may be strings or richer objects; only edit them here if they are strings.
const competitorsEditable = computed(
  () => !!form.value && form.value.competitors.every((c) => typeof c === 'string'),
)

function addTo(list, item) { list.push(item) }
function removeAt(list, i) { list.splice(i, 1) }

function cleanPayload() {
  const d = JSON.parse(JSON.stringify(form.value))
  const strings = (a) => a.map((s) => (typeof s === 'string' ? s.trim() : s)).filter((s) => s !== '')
  for (const k of ['target_customers', 'use_cases', 'security', 'limitations']) d[k] = strings(d[k])
  if (d.competitors.every((c) => typeof c === 'string')) d.competitors = strings(d.competitors)
  d.features = d.features.filter((f) => f.name?.trim())
  d.integrations = d.integrations.filter((f) => f.name?.trim())
  d.faq = d.faq.filter((f) => f.question?.trim())
  d.pricing.plans = d.pricing.plans
    .filter((p) => p.name?.trim())
    .map((p) => ({ ...p, price_per_user_per_month: Number(p.price_per_user_per_month) || 0 }))
  return d
}

async function openProductInfoEditor() {
  productInfoOpen.value = true
  productInfoLoading.value = true
  productInfoSaving.value = false
  productInfoError.value = ''
  productInfoStatus.value = ''
  tab.value = 'overview'
  form.value = null

  try {
    const response = await fetch(`${apiBaseUrl}/product-info`)
    if (!response.ok) throw new Error(`Unable to load product info (${response.status}).`)
    form.value = normalize(await response.json())
  } catch (error) {
    productInfoError.value = error instanceof Error ? error.message : 'Unable to load product info.'
  } finally {
    productInfoLoading.value = false
  }
}

function closeProductInfoEditor() {
  if (productInfoSaving.value) return
  productInfoOpen.value = false
  productInfoError.value = ''
  productInfoStatus.value = ''
}

async function saveProductInfo() {
  if (!form.value) return
  if (!form.value.name.trim()) {
    productInfoError.value = 'Add a product name before saving.'
    productInfoStatus.value = ''
    tab.value = 'overview'
    return
  }

  productInfoSaving.value = true
  productInfoError.value = ''
  productInfoStatus.value = ''
  try {
    const response = await fetch(`${apiBaseUrl}/product-info`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cleanPayload()),
    })
    if (!response.ok) {
      const detail = await response.text()
      throw new Error(detail || `Unable to save product info (${response.status}).`)
    }
    form.value = normalize(await response.json())
    productInfoStatus.value = 'Saved'
  } catch (error) {
    productInfoError.value = error instanceof Error ? error.message : 'Unable to save product info.'
  } finally {
    productInfoSaving.value = false
  }
}

/* ---------- orb events ---------- */
function onSelect(id) {
  if (id === 'start') startSession()
  else if (id === 'stop') stopSession()
  else if (id === 'settings') openProductInfoEditor()
  else emit('select', id)
}

// The orb asks for idle when it's tapped while listening.
function onStateRequest(next) {
  if (next === 'idle' && active.value) stopSession()
}

function onKey(e) {
  if (e.key === 'Escape' && productInfoOpen.value) closeProductInfoEditor()
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  clearTimeout(hideTimer)
  releaseSocket(active.value)
})
</script>

<template>
  <VoiceOrb
    :state="state"
    :subtitle="subtitle"
    :caption="caption"
    :tone="tone"
    :priority="priority"
    :items="items"
    @select="onSelect"
    @update:state="onStateRequest"
  />

  <div v-if="productInfoOpen" class="pm-backdrop" @click.self="closeProductInfoEditor">
    <section class="pm" role="dialog" aria-modal="true" aria-labelledby="pm-title">
      <!-- Sheet header: Cancel · Title · Save -->
      <header class="pm-head">
        <button class="pm-bar" type="button" :disabled="productInfoSaving" @click="closeProductInfoEditor">Cancel</button>
        <h2 id="pm-title">Product info</h2>
        <button
          class="pm-bar strong"
          type="button"
          :disabled="!form || productInfoLoading || productInfoSaving"
          @click="saveProductInfo"
        >{{ productInfoSaving ? 'Saving…' : 'Save' }}</button>
      </header>

      <p v-if="productInfoError" class="pm-msg err" role="alert">{{ productInfoError }}</p>
      <p v-else-if="productInfoStatus" class="pm-msg ok" role="status">{{ productInfoStatus }}</p>

      <nav v-if="form" class="pm-tabs" role="tablist">
        <button
          v-for="t in TABS"
          :key="t.id"
          type="button"
          role="tab"
          :aria-selected="tab === t.id"
          :class="{ on: tab === t.id }"
          @click="tab = t.id"
        >{{ t.label }}</button>
      </nav>

      <div class="pm-body">
        <p v-if="productInfoLoading" class="pm-muted center">Loading…</p>

        <template v-else-if="form">
          <!-- Overview -->
          <div v-show="tab === 'overview'" class="pm-pane">
            <div class="pm-group">
              <label class="pm-cell kv">
                <span>Name</span>
                <input v-model="form.name" type="text" placeholder="Required" />
              </label>
              <label class="pm-cell">
                <textarea v-model="form.description" rows="3" placeholder="Description" aria-label="Description" />
              </label>
              <label class="pm-cell kv">
                <span>Last verified</span>
                <input v-model="form.last_verified" type="date" />
              </label>
            </div>

            <section v-for="l in LISTS.overview" :key="l.key" class="pm-section">
              <h3 class="pm-label">{{ l.label }}</h3>
              <div class="pm-group">
                <div v-for="(_, i) in form[l.key]" :key="i" class="pm-cell">
                  <input v-model="form[l.key][i]" type="text" :aria-label="l.label" />
                  <button class="pm-rm" type="button" :aria-label="`Remove ${l.label}`" @click="removeAt(form[l.key], i)">×</button>
                </div>
                <button class="pm-cell pm-add" type="button" @click="addTo(form[l.key], '')">
                  <i aria-hidden="true">+</i>{{ l.add }}
                </button>
              </div>
            </section>
          </div>

          <!-- Features -->
          <div v-show="tab === 'features'" class="pm-pane">
            <div v-for="(f, i) in form.features" :key="i" class="pm-group">
              <div class="pm-cell">
                <input v-model="f.name" type="text" placeholder="Feature name" aria-label="Feature name" />
                <button class="pm-rm" type="button" aria-label="Remove feature" @click="removeAt(form.features, i)">×</button>
              </div>
              <label class="pm-cell">
                <textarea v-model="f.description" rows="2" placeholder="What it does" aria-label="Feature description" />
              </label>
              <label class="pm-cell kv">
                <span>Verified</span>
                <input v-model="f.verified" class="pm-switch" type="checkbox" role="switch" />
              </label>
            </div>
            <div class="pm-group">
              <button class="pm-cell pm-add" type="button" @click="addTo(form.features, { name: '', description: '', verified: false })">
                <i aria-hidden="true">+</i>Add feature
              </button>
            </div>
          </div>

          <!-- Pricing -->
          <div v-show="tab === 'pricing'" class="pm-pane">
            <div class="pm-group">
              <label class="pm-cell kv">
                <span>Currency</span>
                <input v-model="form.pricing.currency" type="text" maxlength="3" />
              </label>
              <label class="pm-cell kv">
                <span>Verified</span>
                <input v-model="form.pricing.verified" class="pm-switch" type="checkbox" role="switch" />
              </label>
            </div>

            <div v-for="(p, i) in form.pricing.plans" :key="i" class="pm-group">
              <div class="pm-cell">
                <input v-model="p.name" type="text" placeholder="Plan name" aria-label="Plan name" />
                <button class="pm-rm" type="button" aria-label="Remove plan" @click="removeAt(form.pricing.plans, i)">×</button>
              </div>
              <label class="pm-cell kv">
                <span>Per user / month</span>
                <input v-model.number="p.price_per_user_per_month" type="number" min="0" step="any" />
              </label>
              <label class="pm-cell kv">
                <span>Billing</span>
                <select v-model="p.billing_period">
                  <option value="monthly">Monthly</option>
                  <option value="annual">Annual</option>
                </select>
              </label>
              <label class="pm-cell kv">
                <span>Verified</span>
                <input v-model="p.verified" class="pm-switch" type="checkbox" role="switch" />
              </label>
            </div>
            <div class="pm-group">
              <button
                class="pm-cell pm-add"
                type="button"
                @click="addTo(form.pricing.plans, { name: '', price_per_user_per_month: 0, billing_period: 'monthly', verified: false })"
              ><i aria-hidden="true">+</i>Add plan</button>
            </div>

            <section class="pm-section">
              <h3 class="pm-label">Discount policy</h3>
              <div class="pm-group">
                <label class="pm-cell">
                  <textarea v-model="form.pricing.discount_policy" rows="2" aria-label="Discount policy" />
                </label>
              </div>
            </section>
          </div>

          <!-- Integrations -->
          <div v-show="tab === 'integrations'" class="pm-pane">
            <div v-for="(f, i) in form.integrations" :key="i" class="pm-group">
              <div class="pm-cell">
                <input v-model="f.name" type="text" placeholder="Integration name" aria-label="Integration name" />
                <button class="pm-rm" type="button" aria-label="Remove integration" @click="removeAt(form.integrations, i)">×</button>
              </div>
              <label class="pm-cell">
                <textarea v-model="f.description" rows="2" placeholder="What it does" aria-label="Integration description" />
              </label>
              <label class="pm-cell kv">
                <span>Verified</span>
                <input v-model="f.verified" class="pm-switch" type="checkbox" role="switch" />
              </label>
            </div>
            <div class="pm-group">
              <button class="pm-cell pm-add" type="button" @click="addTo(form.integrations, { name: '', description: '', verified: false })">
                <i aria-hidden="true">+</i>Add integration
              </button>
            </div>
          </div>

          <!-- Rollout -->
          <div v-show="tab === 'rollout'" class="pm-pane">
            <div class="pm-group">
              <label class="pm-cell">
                <textarea v-model="form.implementation.process" rows="3" placeholder="Process" aria-label="Process" />
              </label>
              <label class="pm-cell kv">
                <span>Timeline</span>
                <input v-model="form.implementation.timeline" type="text" placeholder="e.g. 2–3 weeks" />
              </label>
              <label class="pm-cell kv">
                <span>Verified</span>
                <input v-model="form.implementation.verified" class="pm-switch" type="checkbox" role="switch" />
              </label>
            </div>
          </div>

          <!-- FAQ -->
          <div v-show="tab === 'faq'" class="pm-pane">
            <div v-for="(q, i) in form.faq" :key="i" class="pm-group">
              <div class="pm-cell">
                <input v-model="q.question" type="text" placeholder="Question" aria-label="Question" />
                <button class="pm-rm" type="button" aria-label="Remove question" @click="removeAt(form.faq, i)">×</button>
              </div>
              <label class="pm-cell">
                <textarea v-model="q.answer" rows="2" placeholder="Answer" aria-label="Answer" />
              </label>
              <label class="pm-cell kv">
                <span>Verified</span>
                <input v-model="q.verified" class="pm-switch" type="checkbox" role="switch" />
              </label>
            </div>
            <div class="pm-group">
              <button class="pm-cell pm-add" type="button" @click="addTo(form.faq, { question: '', answer: '', verified: false })">
                <i aria-hidden="true">+</i>Add question
              </button>
            </div>
          </div>

          <!-- More -->
          <div v-show="tab === 'more'" class="pm-pane">
            <section v-for="l in LISTS.more" :key="l.key" class="pm-section">
              <h3 class="pm-label">{{ l.label }}</h3>
              <div class="pm-group">
                <div v-for="(_, i) in form[l.key]" :key="i" class="pm-cell">
                  <input v-model="form[l.key][i]" type="text" :aria-label="l.label" />
                  <button class="pm-rm" type="button" :aria-label="`Remove ${l.label}`" @click="removeAt(form[l.key], i)">×</button>
                </div>
                <button class="pm-cell pm-add" type="button" @click="addTo(form[l.key], '')">
                  <i aria-hidden="true">+</i>{{ l.add }}
                </button>
              </div>
            </section>

            <section class="pm-section">
              <h3 class="pm-label">Competitors</h3>
              <div v-if="competitorsEditable" class="pm-group">
                <div v-for="(_, i) in form.competitors" :key="i" class="pm-cell">
                  <input v-model="form.competitors[i]" type="text" aria-label="Competitor" />
                  <button class="pm-rm" type="button" aria-label="Remove competitor" @click="removeAt(form.competitors, i)">×</button>
                </div>
                <button class="pm-cell pm-add" type="button" @click="addTo(form.competitors, '')">
                  <i aria-hidden="true">+</i>Add competitor
                </button>
              </div>
              <p v-else class="pm-muted note">Competitor details are kept as they are.</p>
            </section>
          </div>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ---------- tokens (system colours, dark by default) ---------- */
.pm {
  --sheet: rgba(28, 28, 30, 0.82);
  --group: rgba(255, 255, 255, 0.07);
  --press: rgba(255, 255, 255, 0.05);
  --sep: rgba(255, 255, 255, 0.09);
  --chip: rgba(255, 255, 255, 0.12);
  --text: #f5f5f7;
  --text2: rgba(235, 235, 245, 0.6);
  --text3: rgba(235, 235, 245, 0.3);
  --blue: #0a84ff;
  --red: #ff453a;
  --green: #30d158;
  --track: rgba(120, 120, 128, 0.36);
}

@media (prefers-color-scheme: light) {
  .pm {
    --sheet: rgba(246, 246, 248, 0.86);
    --group: rgba(255, 255, 255, 0.92);
    --press: rgba(60, 60, 67, 0.05);
    --sep: rgba(60, 60, 67, 0.13);
    --chip: rgba(120, 120, 128, 0.16);
    --text: #1d1d1f;
    --text2: rgba(60, 60, 67, 0.6);
    --text3: rgba(60, 60, 67, 0.3);
    --blue: #007aff;
    --red: #ff3b30;
    --green: #34c759;
    --track: rgba(120, 120, 128, 0.25);
  }
}

/* ---------- sheet ---------- */
.pm-backdrop {
  position: fixed;
  inset: 0;
  z-index: 10;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.pm {
  width: min(520px, 100%);
  height: min(660px, 90vh);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: var(--text);
  background: var(--sheet);
  backdrop-filter: blur(40px) saturate(180%);
  -webkit-backdrop-filter: blur(40px) saturate(180%);
  border-radius: 20px;
  box-shadow: 0 0 0 0.5px var(--sep), 0 30px 80px rgba(0, 0, 0, 0.35);
  font: 400 15px/1.35 -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Inter', 'Helvetica Neue', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  animation: pm-in 0.28s cubic-bezier(0.2, 0.8, 0.2, 1);
}

@keyframes pm-in {
  from { opacity: 0; transform: scale(0.97) translateY(8px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .pm { animation: none; }
}

/* ---------- header ---------- */
.pm-head {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  padding: 14px 16px 10px;
}
.pm-head h2 { margin: 0; font-size: 1.03rem; font-weight: 600; letter-spacing: -0.01em; }

.pm-bar {
  justify-self: start;
  padding: 4px 2px;
  color: var(--blue);
  font: inherit;
  background: none;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
}
.pm-bar.strong { justify-self: end; font-weight: 600; }
.pm-bar:disabled { opacity: 0.35; cursor: default; }
.pm-bar:not(:disabled):active { opacity: 0.55; }

.pm-msg { margin: 0; padding: 0 20px 8px; font-size: 0.8rem; text-align: center; }
.pm-msg.err { color: var(--red); }
.pm-msg.ok { color: var(--green); }

/* ---------- tabs ---------- */
.pm-tabs {
  display: flex;
  gap: 2px;
  padding: 0 14px 10px;
  overflow-x: auto;
  scrollbar-width: none;
}
.pm-tabs::-webkit-scrollbar { display: none; }
.pm-tabs button {
  flex: none;
  padding: 5px 12px;
  color: var(--text2);
  font: inherit;
  font-size: 0.87rem;
  font-weight: 500;
  background: none;
  border: 0;
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.2s, color 0.2s;
}
.pm-tabs button:hover { color: var(--text); }
.pm-tabs button.on { color: var(--text); background: var(--chip); }

/* ---------- body ---------- */
.pm-body { flex: 1; min-height: 0; overflow-y: auto; padding: 4px 16px 28px; }
.pm-pane { display: flex; flex-direction: column; gap: 18px; }
.pm-section { display: flex; flex-direction: column; gap: 6px; }
.pm-label { margin: 0 0 0 14px; color: var(--text2); font-size: 0.8rem; font-weight: 400; }
.pm-muted { margin: 0; color: var(--text2); }
.pm-muted.center { padding: 40px 0; text-align: center; }
.pm-muted.note { margin-left: 14px; font-size: 0.85rem; }

/* ---------- grouped lists ---------- */
.pm-group {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--group);
  border-radius: 12px;
}

.pm-cell {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 0 14px;
  margin: 0;
  color: inherit;
  font: inherit;
  text-align: left;
  background: none;
  border: 0;
  transition: background 0.15s;
}
/* inset hairline between rows */
.pm-cell + .pm-cell::before {
  content: '';
  position: absolute;
  top: 0;
  left: 14px;
  right: 0;
  height: 0.5px;
  background: var(--sep);
}
.pm-cell:focus-within { background: var(--press); }

.pm-cell.kv { justify-content: space-between; }
.pm-cell.kv > span { flex: none; }
.pm-cell.kv input[type='text'],
.pm-cell.kv input[type='number'],
.pm-cell.kv input[type='date'],
.pm-cell.kv select { width: auto; flex: 1; min-width: 0; text-align: right; color: var(--text2); }

.pm input[type='text'],
.pm input[type='number'],
.pm input[type='date'],
.pm select,
.pm textarea {
  box-sizing: border-box;
  width: 100%;
  padding: 11px 0;
  color: var(--text);
  font: inherit;
  background: transparent;
  border: 0;
  outline: none;
  color-scheme: dark light;
}
.pm select { appearance: none; -webkit-appearance: none; cursor: pointer; padding-right: 2px; }
.pm textarea { resize: none; line-height: 1.4; }
.pm input::placeholder,
.pm textarea::placeholder { color: var(--text3); }
.pm input:focus-visible,
.pm select:focus-visible,
.pm textarea:focus-visible { color: var(--text); }

/* ---------- actions ---------- */
.pm-rm {
  flex: none;
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: var(--text2);
  font-size: 0.95rem;
  line-height: 1;
  background: var(--chip);
  border: 0;
  border-radius: 50%;
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.pm-rm:hover { color: #fff; background: var(--red); }

.pm-add { width: 100%; color: var(--blue); cursor: pointer; }
.pm-add i { font-style: normal; font-size: 1.25rem; font-weight: 300; line-height: 1; }
.pm-add:hover,
.pm-add:focus-visible { background: var(--press); outline: none; }

/* ---------- switch ---------- */
.pm-switch {
  flex: none;
  appearance: none;
  -webkit-appearance: none;
  position: relative;
  width: 42px !important;
  height: 26px;
  margin: 0;
  padding: 0 !important;
  background: var(--track) !important;
  border-radius: 13px !important;
  cursor: pointer;
  transition: background 0.22s;
}
.pm-switch::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 22px;
  height: 22px;
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.25);
  transition: transform 0.22s cubic-bezier(0.3, 0.7, 0.3, 1);
}
.pm-switch:checked { background: var(--green) !important; }
.pm-switch:checked::after { transform: translateX(16px); }
.pm-switch:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
</style>