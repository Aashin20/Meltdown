<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import VoiceOrb from './VoiceOrb.vue'
import SuggestionNotes from './SuggestionNotes.vue'
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
const pendingSuggestions = ref({})
const completedSuggestions = ref({})
const sessionId = ref(0)

// product info form
const productInfoOpen = ref(false)
const productInfoLoading = ref(false)
const productInfoSaving = ref(false)
const productInfoError = ref('')
const productInfoStatus = ref('')
const form = ref(null)
const tab = ref('overview')

// generate from paragraph
const generateOpen = ref(false)
const generateLoading = ref(false)
const paragraph = ref('')
const reportLoading = ref(false)
const reportError = ref('')

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
function syncSuggestions(r) {
  if (Object.prototype.hasOwnProperty.call(r, 'pending_suggestions')) {
    if (r.pending_suggestions && typeof r.pending_suggestions === 'object') {
      pendingSuggestions.value = r.pending_suggestions
    }
  }
  if (Object.prototype.hasOwnProperty.call(r, 'completed_suggestions')) {
    if (r.completed_suggestions && typeof r.completed_suggestions === 'object') {
      completedSuggestions.value = r.completed_suggestions
    }
  }
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
  syncSuggestions(r)
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
  active.value = true
pendingSuggestions.value = {}
completedSuggestions.value = {}
sessionId.value++
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
  { id: 'setup', label: 'Setup' },
  { id: 'more', label: 'More' },
]

// string-list fields, grouped by the tab they appear in
const LISTS = {
  overview: [
    { key: 'target_customers', label: 'Target customers', add: 'Add customer' },
    { key: 'use_cases', label: 'Use cases', add: 'Add use case' },
  ],
  more: [
    { key: 'security', label: 'Security', add: 'Add item' },
    { key: 'limitations', label: 'Limitations', add: 'Add limitation' },
  ],
}

function stripVerificationFields(value) {
  if (Array.isArray(value)) return value.map(stripVerificationFields)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => key !== 'verified' && key !== 'last_verified')
      .map(([key, child]) => [key, stripVerificationFields(child)]),
  )
}

function normalize(data) {
  const d = stripVerificationFields(data)
  d.name ??= ''
  d.description ??= ''
  d.target_customers ??= []
  d.features ??= []
  d.use_cases ??= []
  d.pricing = { currency: 'USD', plans: [], discount_policy: '', ...(d.pricing || {}) }
  d.pricing.plans ??= []
  d.integrations ??= []
  d.implementation = { process: '', timeline: '', ...(d.implementation || {}) }
  d.security ??= []
  d.limitations ??= []
  d.faq ??= []
  d.competitors ??= []
  d.evidence ??= []
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
  const clean = stripVerificationFields(d)
  const strings = (a) => a.map((s) => (typeof s === 'string' ? s.trim() : s)).filter((s) => s !== '')
  for (const k of ['target_customers', 'use_cases', 'security', 'limitations']) clean[k] = strings(clean[k])
  if (clean.competitors.every((c) => typeof c === 'string')) clean.competitors = strings(clean.competitors)
  clean.features = clean.features.filter((f) => f.name?.trim())
  clean.integrations = clean.integrations.filter((f) => f.name?.trim())
  clean.faq = clean.faq.filter((f) => f.question?.trim())
  clean.pricing.plans = clean.pricing.plans
    .filter((p) => p.name?.trim())
    .map((p) => ({ ...p, price_per_user_per_month: Number(p.price_per_user_per_month) || 0 }))
  return clean
}

async function openProductInfoEditor() {
  productInfoOpen.value = true
  productInfoLoading.value = true
  productInfoSaving.value = false
  productInfoError.value = ''
  productInfoStatus.value = ''
  generateOpen.value = false
  generateLoading.value = false
  paragraph.value = ''
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
  if (productInfoSaving.value || generateLoading.value) return
  productInfoOpen.value = false
  productInfoError.value = ''
  productInfoStatus.value = ''
}

async function saveProductInfo() {
  if (!form.value) return
  if (!form.value.name.trim()) {
    productInfoError.value = 'Add a product name before saving.'
    productInfoStatus.value = ''
    generateOpen.value = false
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

function toggleGenerate() {
  if (generateLoading.value) return
  generateOpen.value = !generateOpen.value
  productInfoError.value = ''
  productInfoStatus.value = ''
}

async function generateProductInfo() {
  const text = paragraph.value.trim()
  if (!text) return

  generateLoading.value = true
  productInfoError.value = ''
  productInfoStatus.value = ''
  try {
    const response = await fetch(`${apiBaseUrl}/product-info/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paragraph: text }),
    })
    if (!response.ok) {
      // FastAPI's HTTPException returns { "detail": "..." }
      const raw = await response.text()
      let detail = raw
      try { detail = JSON.parse(raw).detail || raw } catch { /* not JSON */ }
      throw new Error(detail || `Unable to generate product info (${response.status}).`)
    }
    form.value = normalize(await response.json())
    tab.value = 'overview'
    generateOpen.value = false
    paragraph.value = ''
    productInfoStatus.value = 'Generated. Review, then Save.'
  } catch (error) {
    productInfoError.value = error instanceof Error ? error.message : 'Unable to generate product info.'
  } finally {
    generateLoading.value = false
  }
}

async function downloadReport() {
  if (reportLoading.value) return
  reportLoading.value = true
  reportError.value = ''
  try {
    const response = await fetch(`${apiBaseUrl}/report`)
    if (!response.ok) {
      const detail = await response.text()
      throw new Error(detail || `Unable to download report (${response.status}).`)
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'sales_call_report.pdf'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    reportError.value = error instanceof Error ? error.message : 'Unable to download report.'
    show('Report download failed', reportError.value, 'warm', 'high', 7000)
  } finally {
    reportLoading.value = false
  }
}

/* ---------- orb events ---------- */
function onSelect(id) {
  if (id === 'start') startSession()
  else if (id === 'stop') stopSession()
  else if (id === 'settings') openProductInfoEditor()
  else if (id === 'pdf') downloadReport()
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
<SuggestionNotes
  :pending="pendingSuggestions"
  :completed="completedSuggestions"
  :reset-key="sessionId"
  :infer-completed="false"
/>
  <div v-if="productInfoOpen" class="pm-backdrop" @click.self="closeProductInfoEditor">
    <section class="pm" role="dialog" aria-modal="true" aria-labelledby="pm-title">
      <!-- Header: Cancel · Title · AI + Save -->
      <header class="pm-head">
        <button class="pm-bar" type="button" :disabled="productInfoSaving || generateLoading" @click="closeProductInfoEditor">Cancel</button>
        <h2 id="pm-title">Product info</h2>
        <div class="pm-right">
          <button
            class="pm-ai"
            type="button"
            title="Generate from a paragraph"
            aria-label="Generate from a paragraph"
            :aria-pressed="generateOpen"
            :class="{ on: generateOpen }"
            :disabled="productInfoLoading || generateLoading"
            @click="toggleGenerate"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">
              <path d="M12 2l1.9 5.6a3 3 0 0 0 1.9 1.9L21.4 11.4 15.8 13.3a3 3 0 0 0-1.9 1.9L12 20.8l-1.9-5.6a3 3 0 0 0-1.9-1.9L2.6 11.4l5.6-1.9a3 3 0 0 0 1.9-1.9L12 2z" />
              <path d="M19 15l.8 2.2a1.5 1.5 0 0 0 1 1l2.2.8-2.2.8a1.5 1.5 0 0 0-1 1L19 23l-.8-2.2a1.5 1.5 0 0 0-1-1L15 19l2.2-.8a1.5 1.5 0 0 0 1-1L19 15z" opacity=".7" transform="translate(-3 -4) scale(.85)" />
            </svg>
          </button>
          <button
            class="pm-bar strong"
            type="button"
            :disabled="!form || productInfoLoading || productInfoSaving || generateLoading"
            @click="saveProductInfo"
          >{{ productInfoSaving ? 'Saving…' : 'Save' }}</button>
        </div>
      </header>

      <p v-if="productInfoError" class="pm-msg err" role="alert">{{ productInfoError }}</p>
      <p v-else-if="productInfoStatus" class="pm-msg ok" role="status">{{ productInfoStatus }}</p>

      <nav v-if="form && !generateOpen" class="pm-tabs" role="tablist">
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

        <!-- Generate -->
        <div v-else-if="generateOpen" class="pm-gen">
          <textarea
            v-model="paragraph"
            :disabled="generateLoading"
            placeholder="Describe your product in a paragraph. Features, pricing, customers, anything you know."
            aria-label="Product paragraph"
          />
          <button
            class="pm-primary"
            type="button"
            :disabled="generateLoading || !paragraph.trim()"
            @click="generateProductInfo"
          >
            <span v-if="generateLoading" class="pm-spin" aria-hidden="true"></span>
            {{ generateLoading ? 'Generating…' : 'Generate' }}
          </button>
        </div>

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
            </div>

            <section v-for="l in LISTS.overview" :key="l.key" class="pm-section">
              <h3 class="pm-label">{{ l.label }}</h3>
              <div class="pm-group">
                <div v-for="(_, i) in form[l.key]" :key="i" class="pm-cell">
                  <input v-model="form[l.key][i]" type="text" :aria-label="l.label" />
                  <button class="pm-rm" type="button" :aria-label="`Remove ${l.label}`" @click="removeAt(form[l.key], i)">×</button>
                </div>
                <button class="pm-cell pm-add" type="button" @click="addTo(form[l.key], '')">{{ l.add }}</button>
              </div>
            </section>
          </div>

          <!-- Features -->
          <div v-show="tab === 'features'" class="pm-pane">
            <div v-for="(f, i) in form.features" :key="i" class="pm-group">
              <div class="pm-cell">
                <input v-model="f.name" type="text" placeholder="Feature" aria-label="Feature name" />
                <button class="pm-rm" type="button" aria-label="Remove feature" @click="removeAt(form.features, i)">×</button>
              </div>
              <label class="pm-cell">
                <textarea v-model="f.description" rows="2" placeholder="What it does" aria-label="Feature description" />
              </label>
            </div>
            <div class="pm-group">
              <button class="pm-cell pm-add" type="button" @click="addTo(form.features, { name: '', description: '' })">Add feature</button>
            </div>
          </div>

          <!-- Pricing -->
          <div v-show="tab === 'pricing'" class="pm-pane">
            <div class="pm-group">
              <label class="pm-cell kv">
                <span>Currency</span>
                <input v-model="form.pricing.currency" type="text" maxlength="3" />
              </label>
            </div>

            <div v-for="(p, i) in form.pricing.plans" :key="i" class="pm-group">
              <div class="pm-cell">
                <input v-model="p.name" type="text" placeholder="Plan" aria-label="Plan name" />
                <button class="pm-rm" type="button" aria-label="Remove plan" @click="removeAt(form.pricing.plans, i)">×</button>
              </div>
              <div class="pm-cell kv">
                <span>Per user</span>
                <input v-model.number="p.price_per_user_per_month" type="number" min="0" step="any" aria-label="Price per user per month" />
                <select v-model="p.billing_period" aria-label="Billing period">
                  <option value="monthly">/ month</option>
                  <option value="annual">/ year</option>
                </select>
              </div>
            </div>
            <div class="pm-group">
              <button
                class="pm-cell pm-add"
                type="button"
                @click="addTo(form.pricing.plans, { name: '', price_per_user_per_month: 0, billing_period: 'monthly' })"
              >Add plan</button>
            </div>

            <div class="pm-group">
              <label class="pm-cell">
                <textarea v-model="form.pricing.discount_policy" rows="2" placeholder="Discount policy" aria-label="Discount policy" />
              </label>
            </div>
          </div>

          <!-- Setup: rollout + integrations -->
          <div v-show="tab === 'setup'" class="pm-pane">
            <div class="pm-group">
              <label class="pm-cell">
                <textarea v-model="form.implementation.process" rows="3" placeholder="Rollout process" aria-label="Process" />
              </label>
              <label class="pm-cell kv">
                <span>Timeline</span>
                <input v-model="form.implementation.timeline" type="text" placeholder="e.g. 2–3 weeks" />
              </label>
            </div>

            <section class="pm-section">
              <h3 class="pm-label">Integrations</h3>
              <div v-for="(f, i) in form.integrations" :key="i" class="pm-group">
                <div class="pm-cell">
                  <input v-model="f.name" type="text" placeholder="Integration" aria-label="Integration name" />
                  <button class="pm-rm" type="button" aria-label="Remove integration" @click="removeAt(form.integrations, i)">×</button>
                </div>
                <label class="pm-cell">
                  <textarea v-model="f.description" rows="2" placeholder="What it does" aria-label="Integration description" />
                </label>
              </div>
              <div class="pm-group">
                <button class="pm-cell pm-add" type="button" @click="addTo(form.integrations, { name: '', description: '' })">Add integration</button>
              </div>
            </section>
          </div>

          <!-- More: FAQ, security, limitations, competitors -->
          <div v-show="tab === 'more'" class="pm-pane">
            <section class="pm-section">
              <h3 class="pm-label">FAQ</h3>
              <div v-for="(q, i) in form.faq" :key="i" class="pm-group">
                <div class="pm-cell">
                  <input v-model="q.question" type="text" placeholder="Question" aria-label="Question" />
                  <button class="pm-rm" type="button" aria-label="Remove question" @click="removeAt(form.faq, i)">×</button>
                </div>
                <label class="pm-cell">
                  <textarea v-model="q.answer" rows="2" placeholder="Answer" aria-label="Answer" />
                </label>
              </div>
              <div class="pm-group">
                <button class="pm-cell pm-add" type="button" @click="addTo(form.faq, { question: '', answer: '' })">Add question</button>
              </div>
            </section>

            <section v-for="l in LISTS.more" :key="l.key" class="pm-section">
              <h3 class="pm-label">{{ l.label }}</h3>
              <div class="pm-group">
                <div v-for="(_, i) in form[l.key]" :key="i" class="pm-cell">
                  <input v-model="form[l.key][i]" type="text" :aria-label="l.label" />
                  <button class="pm-rm" type="button" :aria-label="`Remove ${l.label}`" @click="removeAt(form[l.key], i)">×</button>
                </div>
                <button class="pm-cell pm-add" type="button" @click="addTo(form[l.key], '')">{{ l.add }}</button>
              </div>
            </section>

            <section class="pm-section">
              <h3 class="pm-label">Competitors</h3>
              <div v-if="competitorsEditable" class="pm-group">
                <div v-for="(_, i) in form.competitors" :key="i" class="pm-cell">
                  <input v-model="form.competitors[i]" type="text" aria-label="Competitor" />
                  <button class="pm-rm" type="button" aria-label="Remove competitor" @click="removeAt(form.competitors, i)">×</button>
                </div>
                <button class="pm-cell pm-add" type="button" @click="addTo(form.competitors, '')">Add competitor</button>
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
/* Keep the page surface black while the orb remains independently layered above it. */
:global(html),
:global(body),
:global(#app) {
  min-height: 100%;
  margin: 0;
  background: #000;
}
:global(html),
:global(body),
:global(#app) {
  min-height: 100%;
  margin: 0;
  background: #000;
}
/* ---------- tokens (system colours, dark by default) ---------- */
.pm {
  --sheet: rgba(28, 28, 30, 0.82);
  --group: rgba(255, 255, 255, 0.05);
  --press: rgba(255, 255, 255, 0.04);
  --sep: rgba(255, 255, 255, 0.08);
  --chip: rgba(255, 255, 255, 0.1);
  --text: #f5f5f7;
  --text2: rgba(235, 235, 245, 0.55);
  --text3: rgba(235, 235, 245, 0.28);
  --blue: #0a84ff;
  --red: #ff453a;
  --green: #30d158;
}

@media (prefers-color-scheme: light) {
  .pm {
    --sheet: rgba(246, 246, 248, 0.88);
    --group: rgba(255, 255, 255, 0.85);
    --press: rgba(60, 60, 67, 0.04);
    --sep: rgba(60, 60, 67, 0.12);
    --chip: rgba(120, 120, 128, 0.14);
    --text: #1d1d1f;
    --text2: rgba(60, 60, 67, 0.55);
    --text3: rgba(60, 60, 67, 0.28);
    --blue: #007aff;
    --red: #ff3b30;
    --green: #34c759;
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
  width: min(480px, 100%);
  height: min(620px, 90vh);
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
  padding: 14px 16px 8px;
}
.pm-head h2 { margin: 0; font-size: 1rem; font-weight: 600; letter-spacing: -0.01em; }
.pm-right { justify-self: end; display: flex; align-items: center; gap: 14px; }

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
.pm-bar.strong { font-weight: 600; }
.pm-bar:disabled { opacity: 0.35; cursor: default; }
.pm-bar:not(:disabled):active { opacity: 0.55; }

.pm-ai {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  color: var(--blue);
  background: none;
  border: 0;
  border-radius: 50%;
  cursor: pointer;
  transition: background 0.2s;
}
.pm-ai:hover { background: var(--chip); }
.pm-ai.on { background: var(--chip); }
.pm-ai:disabled { opacity: 0.35; cursor: default; }

.pm-msg { margin: 0; padding: 0 20px 6px; font-size: 0.78rem; text-align: center; }
.pm-msg.err { color: var(--red); }
.pm-msg.ok { color: var(--green); }

/* ---------- tabs ---------- */
.pm-tabs {
  display: flex;
  justify-content: center;
  gap: 18px;
  padding: 0 16px;
  border-bottom: 0.5px solid var(--sep);
  overflow-x: auto;
  scrollbar-width: none;
}
.pm-tabs::-webkit-scrollbar { display: none; }
.pm-tabs button {
  flex: none;
  padding: 8px 0 9px;
  color: var(--text2);
  font: inherit;
  font-size: 0.85rem;
  background: none;
  border: 0;
  border-bottom: 1.5px solid transparent;
  margin-bottom: -0.5px;
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s;
}
.pm-tabs button:hover { color: var(--text); }
.pm-tabs button.on { color: var(--text); border-bottom-color: var(--text); }

/* ---------- body ---------- */
.pm-body { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 16px 24px; display: flex; flex-direction: column; }
.pm-pane { display: flex; flex-direction: column; gap: 14px; }
.pm-section { display: flex; flex-direction: column; gap: 6px; }
.pm-label { margin: 8px 0 0 4px; color: var(--text2); font-size: 0.75rem; font-weight: 500; letter-spacing: 0.02em; }
.pm-muted { margin: 0; color: var(--text2); }
.pm-muted.center { padding: 40px 0; text-align: center; }
.pm-muted.note { margin-left: 4px; font-size: 0.85rem; }

/* ---------- grouped rows ---------- */
.pm-group {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--group);
  border-radius: 10px;
}

.pm-cell {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 0 14px;
  margin: 0;
  color: inherit;
  font: inherit;
  text-align: left;
  background: none;
  border: 0;
}
.pm-cell + .pm-cell::before {
  content: '';
  position: absolute;
  top: 0;
  left: 14px;
  right: 0;
  height: 0.5px;
  background: var(--sep);
}

.pm-cell.kv { justify-content: space-between; }
.pm-cell.kv > span { flex: none; color: var(--text2); }
.pm-cell.kv input[type='text'],
.pm-cell.kv input[type='number'],
.pm-cell.kv select { width: auto; flex: 1; min-width: 0; text-align: right; }
.pm-cell.kv select { flex: none; color: var(--text2); }

.pm input[type='text'],
.pm input[type='number'],
.pm select,
.pm textarea {
  box-sizing: border-box;
  width: 100%;
  padding: 10px 0;
  color: var(--text);
  font: inherit;
  background: transparent;
  border: 0;
  outline: none;
  color-scheme: dark light;
}
.pm select { appearance: none; -webkit-appearance: none; cursor: pointer; }
.pm textarea { resize: none; line-height: 1.4; }
.pm input::placeholder,
.pm textarea::placeholder { color: var(--text3); }

/* ---------- row actions ---------- */
.pm-rm {
  flex: none;
  padding: 0 2px;
  color: var(--text3);
  font-size: 1.1rem;
  line-height: 1;
  background: none;
  border: 0;
  cursor: pointer;
  transition: color 0.15s;
}
.pm-rm:hover { color: var(--red); }

.pm-add { width: 100%; color: var(--blue); font-size: 0.9rem; cursor: pointer; }
.pm-add:hover,
.pm-add:focus-visible { background: var(--press); outline: none; }

/* ---------- generate ---------- */
.pm-gen { flex: 1; display: flex; flex-direction: column; gap: 12px; min-height: 0; }
.pm-gen textarea {
  flex: 1;
  min-height: 0;
  padding: 14px;
  background: var(--group);
  border-radius: 10px;
}
.pm-gen textarea:disabled { opacity: 0.5; }

.pm-primary {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex: none;
  width: 100%;
  min-height: 44px;
  color: #fff;
  font: inherit;
  font-weight: 600;
  background: var(--blue);
  border: 0;
  border-radius: 10px;
  cursor: pointer;
}
.pm-primary:disabled { opacity: 0.4; cursor: default; }
.pm-primary:not(:disabled):active { opacity: 0.8; }

.pm-spin {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.35);
  border-top-color: #fff;
  border-radius: 50%;
  animation: pm-spin 0.7s linear infinite;
}
@keyframes pm-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .pm-spin { animation-duration: 1.6s; }
}
</style>