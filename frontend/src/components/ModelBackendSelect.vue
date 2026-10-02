<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
const props = defineProps<{ modelValue:'local'|'external'|'mock'; externalConfigured:boolean; compact?:boolean }>()
const emit = defineEmits<{ 'update:modelValue':[value:'local'|'external'|'mock'] }>()
const root = ref<HTMLElement|null>(null), open = ref(false)
const options = computed(() => [
  { value:'local' as const, icon:'✦', label:'本地模型', disabled:false },
  { value:'external' as const, icon:'☁', label:'外部 API', disabled:!props.externalConfigured },
  { value:'mock' as const, icon:'◇', label:props.compact?'离线演示':'离线演示（Mock）', disabled:false },
])
const selected = computed(() => options.value.find(item=>item.value===props.modelValue) || options.value[0])
function choose(item:typeof options.value[number]) { if(item.disabled)return; emit('update:modelValue',item.value); open.value=false }
function closeOutside(event:PointerEvent) { if(!root.value?.contains(event.target as Node))open.value=false }
onMounted(()=>document.addEventListener('pointerdown',closeOutside))
onBeforeUnmount(()=>document.removeEventListener('pointerdown',closeOutside))
</script>
<template><div ref="root" class="model-picker" :class="{open,compact}">
  <button type="button" class="model-picker-trigger" :aria-expanded="open" aria-haspopup="listbox" @click="open=!open"><span class="model-picker-icon">{{ selected.icon }}</span><span>{{ selected.label }}</span><i></i></button>
  <div v-if="open" class="model-picker-menu" role="listbox"><button v-for="item in options" :key="item.value" type="button" role="option" :aria-selected="item.value===modelValue" :disabled="item.disabled" @click="choose(item)"><span class="model-picker-icon">{{ item.icon }}</span><span><b>{{ item.label }}</b><small v-if="item.disabled">未配置 API</small></span><em v-if="item.value===modelValue">✓</em></button></div>
</div></template>
