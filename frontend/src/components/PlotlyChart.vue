<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

type Dict = Record<string, any>
const props = defineProps<{ artifact: Dict }>()
const chartEl = ref<HTMLElement|null>(null)
let plotly: any = null
const points = computed(() => props.artifact?.data?.[0]?.x?.length || 0)
const isSingleRootPoint = computed(() => props.artifact?.source_tool==='find_root' && points.value<2)
const iteration = computed(() => props.artifact?.data?.[0]?.x?.[0])
const residual = computed(() => props.artifact?.data?.[0]?.y?.[0])
function scientific(value:unknown){ const number=Number(value); return Number.isFinite(number) ? number.toExponential(3) : '不可用' }

async function renderChart(){
  await nextTick()
  if(!chartEl.value || !props.artifact?.data || isSingleRootPoint.value)return
  plotly ||= (await import('plotly.js-dist-min')).default
  const styles=getComputedStyle(document.documentElement)
  const fg=styles.getPropertyValue('--text').trim(), grid=styles.getPropertyValue('--line').trim(), accent=styles.getPropertyValue('--cyan').trim()
  const data=(props.artifact.data || []).map((series:Dict,index:number)=>({...series,line:{...(series.line||{}),color:index===0?accent:series.line?.color},marker:{...(series.marker||{}),color:index===0?accent:series.marker?.color}}))
  await plotly.react(chartEl.value,data,{...(props.artifact.layout||{}),autosize:true,height:292,margin:{l:54,r:20,t:12,b:44},paper_bgcolor:'transparent',plot_bgcolor:'transparent',font:{family:'Inter, system-ui',color:fg,size:11},xaxis:{...(props.artifact.layout?.xaxis||{}),gridcolor:grid,zerolinecolor:grid},yaxis:{...(props.artifact.layout?.yaxis||{}),gridcolor:grid,zerolinecolor:grid},legend:{orientation:'h',y:1.12}},{responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']})
}

watch(()=>props.artifact,renderChart,{deep:true})
onMounted(renderChart)
onBeforeUnmount(()=>{ if(chartEl.value && plotly)plotly.purge(chartEl.value) })
</script>

<template>
  <section v-if="isSingleRootPoint" class="root-convergence-summary">
    <div><span>✓</span></div>
    <p><b>求根已收敛</b><small>第 {{ iteration }} 次迭代完成 · 残差 {{ scientific(residual) }}</small></p>
  </section>
  <section v-else class="result-chart event-chart">
    <div class="section-title"><div><span>VISUAL OUTPUT</span><h2>{{ artifact.title }}</h2></div><span>Plotly · 交互图</span></div>
    <div ref="chartEl" class="plot"></div>
  </section>
</template>
