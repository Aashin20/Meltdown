import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'

if (window.desktopBridge) document.documentElement.classList.add('desktop')

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')
