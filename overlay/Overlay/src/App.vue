<script setup>
import { onMounted, ref } from 'vue'
import CopilotOrb from './CopilotOrb.vue'
import { useOverlayWindow } from './useOverlayWindow'

const backendUrl = ref('http://localhost:8000')
onMounted(async () => {
  if (window.desktopBridge) {
    backendUrl.value = await window.desktopBridge.getBackendUrl()
    useOverlayWindow()
  }
})
</script>

<template>
  <CopilotOrb :backend-url="backendUrl" />
</template>
