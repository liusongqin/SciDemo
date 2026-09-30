<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import { katex } from '@mdit/plugin-katex'
import DOMPurify from 'dompurify'

type Dict = Record<string, any>
type AgentEvent = { task_id:string; sequence:number; timestamp:string; event_type:string; node?:string; title:string; summary:string; payload:Dict; duration_ms?:number }
type Task = { task_id:string; user_query:string; status:string; plan:Dict[]; agent_steps:Dict[]; tool_calls:Dict[]; model_calls:Dict[]; model_provider:string; verification_results:Dict[]; artifacts:Dict[]; final_answer?:string; retry_count:number; parsed_problem:Dict }
type Example = { id:string; title:string; query:string; method:string }

const md = new MarkdownIt({ html:false, linkify:true, typographer:true, breaks:true }).use(katex)
const examples = ref<Example[]>([])
const query = ref('使用 Newton 法求解 cos(x) - x = 0，初值 0.5，并显示迭代和残差')
const task = ref<Task|null>(null)
const events = ref<AgentEvent[]>([])
const selected = ref<AgentEvent|null>(null)
const modelStatus = ref<Dict|null>(null)
const useModel = ref(true)
const teaching = ref(false)
const tolerance = ref(1e-8)
const retries = ref(2)
const busy = ref(false)
const showSettings = ref(false)
const showInspector = ref(false)
const chartEl = ref<HTMLElement|null>(null)
const composer = ref<HTMLTextAreaElement|null>(null)
let source: EventSource|null = null
let poller: number|undefined
let plotly: any = null

const taskDone = computed(() => ['completed','failed','rejected'].includes(task.value?.status || ''))
const modelOnline = computed(() => Boolean(modelStatus.value?.available))
const latestArtifact = computed(() => task.value?.artifacts?.at(-1))
const answerHtml = computed(() => DOMPurify.sanitize(md.render(task.value?.final_answer || '')))
const elapsed = computed(() => {
  if (!events.value.length) return '—'
  const start = Date.parse(events.value[0].timestamp)
  const end = Date.parse(events.value[events.value.length - 1].timestamp)
  return `${Math.max(0, (end-start)/1000).toFixed(1)} s`
})
const toolCount = computed(() => task.value?.tool_calls?.length || events.value.filter(e=>e.event_type==='tool_started').length)
const currentTitle = computed(() => task.value ? short(task.value.user_query, 29) : '新的科学计算')
const selectedPayload = computed(() => JSON.stringify(selected.value?.payload || {}, null, 2))
const graphEvents = computed(() => events.value.filter(e => ['model_completed','agent_decision','tool_completed','tool_failed','verification_completed','artifact_created','human_review_required','workflow_completed','workflow_failed'].includes(e.event_type)).slice(-16))
const graphNodes = computed(() => graphEvents.value.map((event,index) => {
  const row=Math.floor(index/4), offset=index%4, order=row%2===0?offset:3-offset
  const state=event.event_type.includes('failed') || (event.event_type==='verification_completed'&&!event.payload?.passed) ? 'failed' : event.event_type==='human_review_required' ? 'review' : 'done'
  return {id:`event-${event.sequence}`,label:short(event.title,18),hint:short(event.summary,24),x:105+order*210,y:52+row*112,state,event,index}
}))
const graphEdges = computed(() => graphNodes.value.slice(1).map((node,index) => {
  const previous=graphNodes.value[index]
  return {id:`edge-${node.id}`,d:`M ${previous.x} ${previous.y} L ${node.x} ${node.y}`}
}))
const graphHeight = computed(() => Math.max(125,Math.ceil(Math.max(1,graphNodes.value.length)/4)*112))

function short(value:string, length=48){ return value.length > length ? value.slice(0,length)+'…' : value }
function displayTime(value:string){ return new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)) }
function eventIcon(type:string){
  if(type.includes('failed')) return '×'
  if(type.includes('completed') || type==='artifact_created') return '✓'
  if(type.includes('tool')) return '⌘'
  if(type.includes('model')) return '✦'
  if(type.includes('review')) return '◇'
  return '·'
}
function selectEvent(item:AgentEvent){ selected.value=item; showInspector.value=true }
function chooseExample(event:Event){ const item=examples.value.find(e=>e.id===(event.target as HTMLSelectElement).value); if(item) query.value=item.query }
function resizeComposer(){ if(!composer.value)return; composer.value.style.height='auto'; composer.value.style.height=Math.min(160,Math.max(54,composer.value.scrollHeight))+'px' }

async function api<T>(url:string, init?:RequestInit):Promise<T>{
  const response=await fetch(url,init)
  if(!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`)
  return response.json()
}
async function refresh(id:string){
  const data=await api<{task:Task;events:AgentEvent[]}>(`/api/tasks/${id}`)
  task.value=data.task; events.value=data.events
  busy.value=!['completed','failed','waiting_review','rejected'].includes(data.task.status)
}
function listen(id:string){
  source?.close()
  const after=events.value.at(-1)?.sequence || 0
  source=new EventSource(`/api/tasks/${id}/events?after=${after}`)
  source.onmessage=async event=>{
    const item=JSON.parse(event.data) as AgentEvent
    if(!events.value.some(e=>e.sequence===item.sequence)) events.value.push(item)
    await refresh(id)
    if(['workflow_completed','workflow_failed'].includes(item.event_type)) source?.close()
  }
  source.onerror=()=>{ if(!taskDone.value) window.setTimeout(()=>listen(id),1800) }
}
async function restore(id:string){
  try { await refresh(id); if(!taskDone.value) listen(id) }
  catch { localStorage.removeItem('scidemo-task') }
}
async function run(){
  if(query.value.trim().length<3 || busy.value)return
  busy.value=true; task.value=null; events.value=[]; selected.value=null
  try{
    const data=await api<{task_id:string}>('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:query.value.trim(),teaching_mode:teaching.value,require_review:teaching.value,use_local_model:useModel.value,tolerance:tolerance.value,max_retries:retries.value})})
    localStorage.setItem('scidemo-task',data.task_id)
    await refresh(data.task_id); listen(data.task_id)
  }catch(error){
    busy.value=false
    selected.value={task_id:'',sequence:0,timestamp:new Date().toISOString(),event_type:'failed',title:'无法创建任务',summary:String(error),payload:{}}
    showInspector.value=true
  }
}
async function review(action:'approve'|'modify'|'reject'){
  if(!task.value)return
  busy.value=true
  await api(`/api/tasks/${task.value.task_id}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,parameters:{tolerance:tolerance.value},comment:''})})
  listen(task.value.task_id); window.setTimeout(()=>task.value && refresh(task.value.task_id),150)
}
function newChat(){ source?.close(); task.value=null; events.value=[]; selected.value=null; busy.value=false; localStorage.removeItem('scidemo-task'); nextTick(()=>composer.value?.focus()) }
function onComposerKey(e:KeyboardEvent){ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); run() } }

async function renderChart(){
  await nextTick()
  if(!chartEl.value || !latestArtifact.value)return
  plotly ||= (await import('plotly.js-dist-min')).default
  const styles=getComputedStyle(document.documentElement)
  const fg=styles.getPropertyValue('--text').trim(), grid=styles.getPropertyValue('--line').trim(), accent=styles.getPropertyValue('--cyan').trim()
  const data=(latestArtifact.value.data || []).map((series:Dict,index:number)=>({...series,line:{...(series.line||{}),color:index===0?accent:undefined},marker:{...(series.marker||{}),color:index===0?accent:undefined}}))
  await plotly.react(chartEl.value,data,{...(latestArtifact.value.layout||{}),autosize:true,height:292,margin:{l:54,r:20,t:12,b:44},paper_bgcolor:'transparent',plot_bgcolor:'transparent',font:{family:'Inter, system-ui',color:fg,size:11},xaxis:{...(latestArtifact.value.layout?.xaxis||{}),gridcolor:grid,zerolinecolor:grid},yaxis:{...(latestArtifact.value.layout?.yaxis||{}),gridcolor:grid,zerolinecolor:grid},legend:{orientation:'h',y:1.12}},{responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']})
}

watch(latestArtifact, renderChart, {deep:true})
watch(query, ()=>nextTick(resizeComposer))
onMounted(async()=>{
  const results=await Promise.allSettled([api<Example[]>('/api/examples'),api<Dict>('/api/model/status')])
  if(results[0].status==='fulfilled') examples.value=results[0].value
  if(results[1].status==='fulfilled') modelStatus.value=results[1].value
  const id=localStorage.getItem('scidemo-task'); if(id) await restore(id)
  poller=window.setInterval(()=>task.value && !taskDone.value && refresh(task.value.task_id),2200)
  resizeComposer(); renderChart()
})
onBeforeUnmount(()=>{ source?.close(); if(poller)clearInterval(poller); if(chartEl.value && plotly)plotly.purge(chartEl.value) })
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand"><div class="brand-mark"><i></i><i></i><i></i></div><span>SciAgent</span><em>LAB</em></div>
      <div class="top-center"><span class="crumb">工作区</span><span class="slash">/</span><strong>{{ currentTitle }}</strong></div>
      <div class="top-actions">
        <div class="model-state" :class="{offline:!modelOnline}"><span></span>{{ modelOnline ? (modelStatus?.configured_model || modelStatus?.model || 'Qwen3.5-9B') : '模型离线' }}</div>
        <button class="icon-btn" aria-label="设置" @click="showSettings=!showSettings">⌘</button>
        <button class="new-button" @click="newChat"><span>＋</span> 新会话</button>
      </div>
    </header>

    <main class="workspace">
      <aside class="history-pane">
        <div class="pane-head"><span>会话</span><button aria-label="新会话" @click="newChat">＋</button></div>
        <div class="history-scroll">
          <div class="history-label">当前</div>
          <button class="history-item active">
            <span class="history-icon">∿</span><span><b>{{ currentTitle }}</b><small>{{ task ? `${events.length} 条执行事件` : '等待输入问题' }}</small></span><i>•••</i>
          </button>
          <div class="history-label">快速开始</div>
          <button v-for="item in examples.slice(0,6)" :key="item.id" class="history-item" @click="query=item.query;nextTick(()=>composer?.focus())">
            <span class="history-icon">⌁</span><span><b>{{ item.title }}</b><small>{{ item.method }}</small></span>
          </button>
        </div>
        <div class="runtime-card">
          <div><span class="pulse-dot" :class="{off:!modelOnline}"></span><b>本地运行时</b><em>{{ modelOnline?'READY':'OFFLINE' }}</em></div>
          <p>Qwen3.5-9B · 2 × RTX 3090</p>
          <div class="usage"><span style="width:62%"></span></div>
        </div>
      </aside>

      <section class="chat-pane">
        <div class="chat-scroll">
          <div v-if="!task" class="welcome">
            <div class="orb"><span>∑</span></div>
            <p class="eyebrow">SCIENTIFIC COMPUTING AGENT</p>
            <h1>从问题到<span>可验证结果</span></h1>
            <p>描述方程、数据或数值实验。Agent 会规划计算、调用受控工具、验证结果，并生成可检查的图表。</p>
            <div class="starter-grid">
              <button v-for="item in examples.slice(0,4)" :key="item.id" @click="query=item.query;nextTick(()=>composer?.focus())"><i>↗</i><b>{{ item.title }}</b><span>{{ short(item.query,42) }}</span></button>
            </div>
          </div>

          <template v-else>
            <div class="message user-message"><div class="avatar user-avatar">你</div><div><div class="message-meta"><b>你</b><span>刚刚</span></div><p>{{ task.user_query }}</p></div></div>
            <div class="message agent-message">
              <div class="avatar agent-avatar">∑</div>
              <div class="message-body">
                <div class="message-meta"><b>SciAgent</b><span v-if="busy" class="thinking"><i></i> 正在计算</span><span v-else>{{ task.status==='completed'?'已完成':task.status }}</span></div>

                <div v-if="events.length" class="activity-card">
                  <div class="activity-top"><span class="activity-glyph">⌘</span><div><b>{{ busy ? (events.at(-1)?.title || '正在运行工作流') : '执行记录' }}</b><small>{{ busy ? events.at(-1)?.summary : `${events.length} 个事件 · ${toolCount} 次工具调用` }}</small></div><span class="activity-time">{{ elapsed }}</span></div>
                  <div class="activity-steps">
                    <button v-for="event in events.filter(e=>['agent_decision','tool_completed','verification_completed','artifact_created'].includes(e.event_type)).slice(-5)" :key="event.sequence" @click="selectEvent(event)"><i :class="event.event_type">{{ eventIcon(event.event_type) }}</i><span><b>{{ event.title }}</b><small>{{ short(event.summary,60) }}</small></span><em v-if="event.duration_ms">{{ event.duration_ms.toFixed(0) }}ms</em></button>
                  </div>
                </div>

                <div v-if="task.status==='waiting_review'" class="review-card">
                  <div><b>需要确认计算方案</b><span>Agent 已暂停。检查工具与参数后继续执行。</span></div>
                  <div><button @click="review('reject')">终止</button><button @click="review('modify')">应用参数</button><button class="approve" @click="review('approve')">批准并继续</button></div>
                </div>

                <article v-if="task.final_answer" class="markdown-body" v-html="answerHtml"></article>

                <section v-if="latestArtifact" class="result-chart">
                  <div class="section-title"><div><span>VISUAL OUTPUT</span><h2>{{ latestArtifact.title }}</h2></div><span>Plotly · 交互图</span></div>
                  <div ref="chartEl" class="plot"></div>
                </section>

                <div v-if="task.verification_results?.length" class="verification-strip" :class="{failed:!task.verification_results.at(-1)?.passed}">
                  <span>{{ task.verification_results.at(-1)?.passed ? '✓' : '!' }}</span><div><b>{{ task.verification_results.at(-1)?.passed ? '结果已通过独立验证' : '本轮验证未通过' }}</b><small>{{ task.verification_results.at(-1)?.name }} · 误差 {{ Number(task.verification_results.at(-1)?.value).toExponential(3) }} · 容差 {{ task.verification_results.at(-1)?.tolerance }}</small></div>
                </div>
              </div>
            </div>
          </template>
        </div>

        <div class="composer-wrap">
          <div class="composer" :class="{busy}">
            <textarea ref="composer" v-model="query" rows="1" placeholder="输入一个科学计算问题…" @keydown="onComposerKey" @input="resizeComposer"></textarea>
            <div class="composer-bar">
              <div><select aria-label="示例" @change="chooseExample"><option value="">示例问题</option><option v-for="item in examples" :key="item.id" :value="item.id">{{ item.title }}</option></select><button :class="{on:teaching}" @click="teaching=!teaching">◇ 审核</button><button :class="{on:useModel}" @click="useModel=!useModel">✦ 本地 Qwen</button></div>
              <div><span>{{ query.length }}/2000</span><button class="send" :disabled="busy||query.trim().length<3" @click="run">{{ busy?'···':'↑' }}</button></div>
            </div>
          </div>
          <p>Enter 发送 · Shift + Enter 换行 · 结果由程序验证，仍需结合问题条件判断</p>
        </div>
      </section>

      <aside class="graph-pane">
        <div class="graph-head"><div><span>AGENT TRACE</span><h2>执行图</h2></div><div><span class="live-dot"></span>{{ busy?'LIVE':'TRACE' }}</div></div>
        <div class="graph-canvas dynamic-canvas">
          <svg :viewBox="`0 0 840 ${graphHeight}`" role="img" aria-label="Agent 动态决策轨迹">
            <defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>
            <path v-for="edge in graphEdges" :key="edge.id" class="edge" :d="edge.d"/>
            <g v-for="node in graphNodes" :key="node.id" class="graph-node" :class="node.state" :transform="`translate(${node.x-65} ${node.y-31})`" @click="selectEvent(node.event)">
              <rect width="130" height="62" rx="12"/><circle cx="18" cy="18" r="5"/><text x="18" y="34" class="node-number">{{ String(node.index+1).padStart(2,'0') }}</text><text x="38" y="25" class="node-label">{{ node.label }}</text><text x="38" y="44" class="node-hint">{{ node.state==='review'?'等待确认':node.hint }}</text>
            </g>
            <g v-if="!graphNodes.length" class="graph-node idle" transform="translate(355 21)"><rect width="130" height="62" rx="12"/><circle cx="18" cy="18" r="5"/><text x="18" y="34" class="node-number">00</text><text x="38" y="25" class="node-label">等待任务</text><text x="38" y="44" class="node-hint">轨迹将动态生成</text></g>
          </svg>
          <div class="graph-legend"><span><i class="done"></i>完成</span><span><i class="running"></i>运行中</span><span><i></i>等待</span><span><i class="failed"></i>异常</span></div>
        </div>

        <div class="trace-section">
          <div class="trace-title"><b>运行事件</b><span>{{ events.length }}</span></div>
          <div v-if="events.length" class="trace-list">
            <button v-for="event in events.slice().reverse().slice(0,12)" :key="event.sequence" :class="{selected:selected?.sequence===event.sequence}" @click="selectEvent(event)"><i :class="event.event_type">{{ eventIcon(event.event_type) }}</i><div><b>{{ event.title }}</b><span>{{ short(event.summary,42) }}</span></div><time>{{ displayTime(event.timestamp) }}</time></button>
          </div>
          <div v-else class="empty-trace"><span>⌁</span><p>运行任务后，这里会实时显示模型、工具和验证器的状态。</p></div>
        </div>
      </aside>
    </main>

    <div v-if="showInspector" class="drawer-backdrop" @click.self="showInspector=false">
      <aside class="inspector"><header><div><span>EVENT {{ selected?.sequence }}</span><h2>{{ selected?.title }}</h2></div><button @click="showInspector=false">×</button></header><p>{{ selected?.summary }}</p><div class="inspector-meta"><span>{{ selected?.node || 'system' }}</span><span>{{ selected?.event_type }}</span><span v-if="selected?.duration_ms">{{ selected.duration_ms.toFixed(1) }} ms</span></div><h3>结构化载荷</h3><pre>{{ selectedPayload }}</pre></aside>
    </div>

    <div v-if="showSettings" class="popover settings-popover">
      <div class="popover-head"><b>运行设置</b><button @click="showSettings=false">×</button></div>
      <label><span>验证容差</span><input v-model.number="tolerance" type="number" step="1e-8" min="1e-14" max="0.1"></label>
      <label><span>最大重试次数</span><input v-model.number="retries" type="number" min="0" max="5"></label>
      <label class="toggle"><span>使用本地 Qwen</span><input v-model="useModel" type="checkbox"></label>
    </div>
  </div>
</template>
