<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import { katex } from '@mdit/plugin-katex'
import DOMPurify from 'dompurify'
import PlotlyChart from './components/PlotlyChart.vue'

type Dict = Record<string, any>
type AgentEvent = { task_id:string; sequence:number; timestamp:string; event_type:string; node?:string; title:string; summary:string; payload:Dict; duration_ms?:number }
type Task = { task_id:string; user_query:string; status:string; plan:Dict[]; agent_steps:Dict[]; tool_calls:Dict[]; model_calls:Dict[]; model_provider:string; verification_results:Dict[]; artifacts:Dict[]; final_answer?:string; retry_count:number; parsed_problem:Dict; active_agent?:string; agent_handoffs?:Dict[] }
type Example = { id:string; title:string; query:string; method:string }

const md = new MarkdownIt({ html:false, linkify:true, typographer:true, breaks:true }).use(katex)
const toolCatalog = [
  ['simplify_expression','表达式化简','使用符号规则化简表达式'],['transform_expression','表达式变换','展开、因式分解、约分和三角化简'],
  ['calculate_limit','极限计算','单侧与双侧符号极限'],['differentiate_expression','符号求导','一至五阶单变量导数'],
  ['multivariate_derivative','多变量微分','梯度与 Hessian 矩阵'],['integrate_expression','符号积分','不定积分和一维定积分'],
  ['integrate_multiple','多重积分','二重和三重定积分'],['series_expansion','级数展开','指定点 Taylor 级数'],
  ['evaluate_expression','表达式求值','代入变量计算数值'],['solve_symbolic_equation','符号方程求解','单变量方程解析解'],
  ['solve_symbolic_system','符号方程组','线性与非线性方程组'],['find_root','数值求根','Newton、二分与 Brent 方法'],
  ['matrix_calculation','矩阵计算','矩阵运算、特征值和线性系统'],['solve_symbolic_ode','解析微分方程','一阶 ODE 解析解'],
  ['solve_ode','数值微分方程','一阶初值问题数值解'],['numerical_integral','数值积分','自适应积分与交叉检查'],
  ['interpolate_data','数据插值','线性与三次样条插值'],['fit_curve','曲线拟合','多项式最小二乘拟合'],
  ['plot_function','二维函数绘图','单变量显函数图'],['plot_implicit','隐函数绘图','二维隐式方程曲线'],
  ['plot_surface','三维曲面绘图','二元函数三维曲面'],
].map(([name,label,description])=>({name,label,description}))
const toolNames = Object.fromEntries(toolCatalog.map(tool=>[tool.name,tool.label])) as Record<string,string>
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
const composer = ref<HTMLTextAreaElement|null>(null)
let source: EventSource|null = null
let poller: number|undefined

const taskDone = computed(() => ['completed','failed','rejected','cancelled'].includes(task.value?.status || ''))
const taskCompleted = computed(() => task.value?.status==='completed')
const taskCancelled = computed(() => task.value?.status==='cancelled')
const modelOnline = computed(() => Boolean(modelStatus.value?.available))
const latestVerification = computed(() => task.value?.verification_results?.at(-1))
const answerHtml = computed(() => DOMPurify.sanitize(md.render(friendlyText(task.value?.final_answer || ''))))
const elapsed = computed(() => {
  if (!events.value.length) return '—'
  const start = Date.parse(events.value[0].timestamp)
  const end = Date.parse(events.value[events.value.length - 1].timestamp)
  return `${Math.max(0, (end-start)/1000).toFixed(1)} s`
})
const toolCount = computed(() => task.value?.tool_calls?.length || events.value.filter(e=>e.event_type==='tool_started').length)
const currentTitle = computed(() => task.value ? short(task.value.user_query, 29) : '新的科学计算')
const selectedPayload = computed(() => JSON.stringify(selected.value?.payload || {}, null, 2))
const processEvents = computed(() => {
  const visible=events.value.filter(e => ['agent_handoff','model_started','model_completed','model_failed','agent_decision','tool_started','tool_completed','tool_failed','verification_completed','artifact_created','retry_started','human_review_required','human_feedback_received'].includes(e.event_type))
  return visible.filter((event,index) => event.event_type!=='model_started' || !visible.slice(index+1).some(later =>
    later.node===event.node && ['model_completed','model_failed'].includes(later.event_type) && later.payload?.stage===event.payload?.stage))
})
const showRunningPlaceholder = computed(() => {
  const latest=events.value.at(-1)
  return busy.value && (!latest || !processEvents.value.some(event=>event.sequence===latest.sequence))
})
const agentDefinitions = [
  {key:'problem_analyst',name:'问题分析师',icon:'⌕',role:'拆解目标与约束'},
  {key:'scientific_solver',name:'科学求解员',icon:'∿',role:'规划并执行计算'},
  {key:'verification_critic',name:'验证审查员',icon:'✓',role:'独立检查证据'},
  {key:'report_writer',name:'报告撰写员',icon:'✦',role:'汇总可信结论'},
]
const latestHandoff = computed(() => {
  const live=[...events.value].reverse().find(event=>event.event_type==='agent_handoff')?.payload
  return live?.to ? live : task.value?.agent_handoffs?.at(-1)
})
const effectiveActiveAgent = computed(() => task.value ? (latestHandoff.value?.to || task.value.active_agent || 'problem_analyst') : '')
const currentActivity = computed(() => [...events.value].reverse().find(e => !['node_started','node_completed','agent_handoff'].includes(e.event_type)))
const agentCards = computed(() => agentDefinitions.map(agent => {
  const handoffs=task.value?.agent_handoffs || []
  const participated=handoffs.some(item=>item.from===agent.key || item.to===agent.key)
  const active=Boolean(task.value) && busy.value && effectiveActiveAgent.value===agent.key
  const cancelled=taskCancelled.value && effectiveActiveAgent.value===agent.key
  const completed=taskCompleted.value ? participated || agent.key==='report_writer' : handoffs.some(item=>item.from===agent.key)
  return {...agent,state:active?'active':cancelled?'cancelled':completed?'completed':participated?'ready':'waiting',status:active?'工作中':cancelled?'已取消':completed?'已完成':participated?'已就绪':'等待中'}
}))
function agentState(key:string){ return agentCards.value.find(agent=>agent.key===key)?.state || 'waiting' }
function routeActive(from:string,to:string){ return busy.value && latestHandoff.value?.from===from && latestHandoff.value?.to===to }

function short(value:string, length=48){ return value.length > length ? value.slice(0,length)+'…' : value }
function friendlyText(value:string){
  let result=value || ''
  for(const [name,label] of Object.entries(toolNames)) result=result.replace(new RegExp(`\\b${name}\\b`,'g'),`${label}（${name}）`)
  return result
}
function agentName(key?:string){ return agentDefinitions.find(agent=>agent.key===key)?.name || '协作 Agent' }
function scientific(value:unknown){ const number=Number(value); return value!==null && value!=='' && Number.isFinite(number) ? number.toExponential(3) : '不可用' }
function displayTime(value:string){ return new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)) }
function eventIcon(type:string){
  if(type.includes('failed')) return '×'
  if(type.includes('completed') || type==='artifact_created') return '✓'
  if(type.includes('tool')) return '⌘'
  if(type.includes('model') || type==='agent_decision') return '✦'
  if(type.includes('review')) return '◇'
  return '·'
}
function renderMarkdown(value:string){ return DOMPurify.sanitize(md.render(friendlyText(value || ''))) }
function processKind(event:AgentEvent){
  if(event.event_type==='agent_handoff') return 'handoff'
  if(event.event_type==='agent_decision'||event.event_type.includes('model')) return 'model'
  if(event.event_type.includes('tool')) return event.event_type.includes('failed')?'error':'tool'
  if(event.event_type==='verification_completed') return event.payload?.passed?'verify':'error'
  if(event.event_type.includes('review')) return 'review'
  if(event.event_type==='artifact_created') return 'visual'
  if(event.event_type==='retry_started') return 'retry'
  return 'system'
}
function processLabel(event:AgentEvent){
  if(event.node==='problem_analyst' || event.node==='understand_problem') return '问题分析'
  if(event.node==='verification_critic') return '验证审查'
  if(event.node==='report_writer') return '报告生成'
  if(event.node==='scientific_solver' || event.node==='agent_decide' || event.event_type==='agent_decision') return '求解决策'
  return ({handoff:'Agent 交接',model:'模型决策',tool:'工具调用',verify:'程序验证',error:'异常观察',review:'人工审核',visual:'可视化',retry:'重新规划',system:'系统事件'} as Dict)[processKind(event)]
}
function processPayload(event:AgentEvent){
  let payload:Dict=event.payload || {}
  if(event.event_type==='tool_completed') payload={tool:payload.tool,arguments:payload.arguments,result:payload.result,duration_ms:payload.duration_ms}
  if(event.event_type==='artifact_created') payload={title:payload.title,kind:payload.kind,source_tool:payload.source_tool,series:payload.data?.length}
  const value=JSON.stringify(payload,null,2)
  return value.length>14000 ? value.slice(0,14000)+'\n…（界面已截断，完整数据保存在任务记录中）' : value
}
function selectEvent(item:AgentEvent){ selected.value=item; showInspector.value=true }
function resizeComposer(){ if(!composer.value)return; composer.value.style.height='auto'; composer.value.style.height=Math.min(160,Math.max(54,composer.value.scrollHeight))+'px' }

async function api<T>(url:string, init?:RequestInit):Promise<T>{
  const response=await fetch(url,init)
  if(!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`)
  return response.json()
}
async function refresh(id:string){
  const data=await api<{task:Task;events:AgentEvent[]}>(`/api/tasks/${id}`)
  task.value=data.task; events.value=data.events
  busy.value=!['completed','failed','waiting_review','rejected','cancelled'].includes(data.task.status)
}
function listen(id:string){
  source?.close()
  const after=events.value.at(-1)?.sequence || 0
  source=new EventSource(`/api/tasks/${id}/events?after=${after}`)
  source.onmessage=async event=>{
    const item=JSON.parse(event.data) as AgentEvent
    if(!events.value.some(e=>e.sequence===item.sequence)) events.value.push(item)
    await refresh(id)
    if(['workflow_completed','workflow_failed','workflow_cancelled'].includes(item.event_type)) source?.close()
  }
  source.onerror=()=>{ if(!taskDone.value) window.setTimeout(()=>listen(id),1800) }
}
async function restore(id:string){
  try { await refresh(id); if(!taskDone.value) listen(id) }
  catch { localStorage.removeItem('scidemo-task') }
}
async function run(){
  if(query.value.trim().length<3 || busy.value)return
  const submitted=query.value.trim()
  busy.value=true; task.value=null; events.value=[]; selected.value=null
  try{
    const data=await api<{task_id:string}>('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:submitted,teaching_mode:teaching.value,require_review:teaching.value,use_local_model:useModel.value,tolerance:tolerance.value,max_retries:retries.value})})
    localStorage.setItem('scidemo-task',data.task_id)
    query.value=''; await nextTick(); resizeComposer()
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
async function cancelGeneration(){
  if(!task.value || !busy.value)return
  await api(`/api/tasks/${task.value.task_id}/cancel`,{method:'POST'})
  source?.close(); await refresh(task.value.task_id)
}
function newChat(){ source?.close(); task.value=null; events.value=[]; selected.value=null; busy.value=false; localStorage.removeItem('scidemo-task'); nextTick(()=>composer.value?.focus()) }
function onComposerKey(e:KeyboardEvent){ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); run() } }

watch(query, ()=>nextTick(resizeComposer))
onMounted(async()=>{
  const results=await Promise.allSettled([api<Example[]>('/api/examples'),api<Dict>('/api/model/status')])
  if(results[0].status==='fulfilled') examples.value=results[0].value
  if(results[1].status==='fulfilled') modelStatus.value=results[1].value
  const id=localStorage.getItem('scidemo-task'); if(id) await restore(id)
  poller=window.setInterval(()=>task.value && !taskDone.value && refresh(task.value.task_id),2200)
  resizeComposer()
})
onBeforeUnmount(()=>{ source?.close(); if(poller)clearInterval(poller) })
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
          <details class="sidebar-fold">
            <summary><span>快速开始</span><em>{{ examples.length }}</em></summary>
            <button v-for="item in examples" :key="item.id" class="history-item" @click="query=item.query;nextTick(()=>composer?.focus())">
              <span class="history-icon">⌁</span><span><b>{{ item.title }}</b><small>{{ item.method }}</small></span>
            </button>
          </details>
          <details class="sidebar-fold tool-fold">
            <summary><span>系统工具箱</span><em>{{ toolCatalog.length }}</em></summary>
            <div v-for="tool in toolCatalog" :key="tool.name" class="tool-catalog-item"><b>{{ tool.label }}</b><code>{{ tool.name }}</code><p>{{ tool.description }}</p></div>
          </details>
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
          </div>

          <template v-else>
            <div class="message user-message"><div class="avatar user-avatar">你</div><div><div class="message-meta"><b>你</b><span>刚刚</span></div><article class="user-bubble" v-html="renderMarkdown(task.user_query)"></article></div></div>
            <div class="message agent-message">
              <div class="avatar agent-avatar">∑</div>
              <div class="message-body">
                <div class="message-meta"><b>SciAgent</b><span v-if="busy" class="thinking"><i></i> 正在计算</span><span v-else>{{ task.status==='completed'?'已完成':task.status }}</span></div>

                <div v-if="processEvents.length" class="process-wrap">
                  <div class="process-heading"><div><span>AGENT PROCESS</span><b>运行过程</b></div><em>{{ toolCount }} 次工具调用 · {{ elapsed }}</em></div>
                  <div class="process-thread">
                    <section v-for="event in processEvents" :key="event.sequence" class="process-block" :class="processKind(event)">
                      <button class="process-marker" :aria-label="`查看 ${event.title} 详情`" @click="selectEvent(event)">{{ eventIcon(event.event_type) }}</button>
                      <div class="process-content">
                        <div class="process-meta"><span>{{ processLabel(event) }}</span><time>{{ displayTime(event.timestamp) }}</time><em v-if="event.duration_ms">{{ event.duration_ms.toFixed(0) }} ms</em></div>
                        <h3>{{ friendlyText(event.title) }}</h3>
                        <div class="process-markdown" v-html="renderMarkdown(event.summary)"></div>
                        <details v-if="event.event_type==='artifact_created'" class="artifact-details">
                          <summary>查看生成的图表</summary>
                          <PlotlyChart :artifact="event.payload" />
                        </details>
                        <details v-if="Object.keys(event.payload || {}).length">
                          <summary>{{ processKind(event)==='tool' || processKind(event)==='error' ? '查看输入 / 输出' : '查看结构化证据' }}</summary>
                          <pre>{{ processPayload(event) }}</pre>
                        </details>
                      </div>
                    </section>
                    <section v-if="showRunningPlaceholder" class="process-block active"><span class="process-marker"><i></i></span><div class="process-content"><div class="process-meta"><span>运行中</span></div><h3>{{ friendlyText(events.at(-1)?.title || '等待 Agent 决策') }}</h3><p>{{ friendlyText(events.at(-1)?.summary || '') }}</p></div></section>
                  </div>
                </div>

                <div v-if="task.status==='waiting_review'" class="review-card">
                  <div><b>需要确认计算方案</b><span>Agent 已暂停。检查工具与参数后继续执行。</span></div>
                  <div><button @click="review('reject')">终止</button><button @click="review('modify')">应用参数</button><button class="approve" @click="review('approve')">批准并继续</button></div>
                </div>

                <section v-if="task.final_answer" class="final-response">
                  <div class="response-label">
                    <span>✦</span><b>最终回答</b>
                    <em v-if="latestVerification" class="verification-badge" :class="{failed:!latestVerification.passed}" :title="`${latestVerification.name} · 误差 ${scientific(latestVerification.value)} · 容差 ${latestVerification.tolerance}`">{{ latestVerification.passed ? '✓ 已验证' : '! 验证未通过' }}</em>
                  </div>
                  <article class="markdown-body" v-html="answerHtml"></article>
                  <section v-if="task.artifacts?.length" class="final-artifacts">
                    <div class="final-artifacts-heading"><span>VISUAL RESULTS</span><h2>计算图表</h2><em>{{ task.artifacts.length }} 项</em></div>
                    <PlotlyChart v-for="(artifact,index) in task.artifacts" :key="`${artifact.source_tool || artifact.title}-${index}`" :artifact="artifact" />
                  </section>
                </section>
              </div>
            </div>
          </template>
        </div>

        <div class="composer-wrap">
          <div class="composer" :class="{busy}">
            <textarea ref="composer" v-model="query" rows="1" placeholder="输入一个科学计算问题…" @keydown="onComposerKey" @input="resizeComposer"></textarea>
            <div class="composer-bar">
              <div><button :class="{on:teaching}" @click="teaching=!teaching">◇ 人工审核</button><button :class="{on:useModel}" @click="useModel=!useModel">✦ 本地 Qwen</button></div>
              <div><span>{{ query.length }}/2000</span><button v-if="busy" class="cancel-generation" aria-label="停止模型生成" @click="cancelGeneration">■ 停止生成</button><button v-else class="send" :disabled="query.trim().length<3" @click="run">↑</button></div>
            </div>
          </div>
          <p>Enter 发送 · Shift + Enter 换行 · 结果由程序验证，仍需结合问题条件判断</p>
        </div>
      </section>

      <aside class="graph-pane">
        <div class="graph-head"><div><span>MULTI-AGENT ROUTING</span><h2>Agent 协作流转</h2></div><div><span class="live-dot" :class="{settled:!busy,cancelled:taskCancelled}"></span>{{ busy?'流转中':taskCancelled?'已取消':task?'已同步':'待命' }}</div></div>
        <div class="agent-flow">
          <svg viewBox="0 0 420 405" role="img" aria-label="四个大模型 Agent 的协作流转关系">
            <defs><marker id="flow-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z"/></marker></defs>
            <path class="flow-edge" :class="{active:routeActive('problem_analyst','scientific_solver')}" d="M210 75 C210 105 128 102 128 139"/>
            <path class="flow-edge" :class="{active:routeActive('scientific_solver','verification_critic')}" d="M176 174 L244 174"/>
            <path class="flow-edge return" :class="{active:routeActive('verification_critic','scientific_solver')}" d="M286 209 C286 248 134 248 134 209"/>
            <path class="flow-edge" :class="{active:routeActive('scientific_solver','report_writer')}" d="M128 209 C128 278 210 255 210 291"/>
            <g class="flow-agent" :class="agentState('problem_analyst')" transform="translate(150 25)"><circle cx="60" cy="35" r="31"/><text class="flow-icon" x="60" y="40">⌕</text><text class="flow-name" x="60" y="84">问题分析 Agent</text><text class="flow-role" x="60" y="100">理解目标 · 制定任务</text></g>
            <g class="flow-agent" :class="agentState('scientific_solver')" transform="translate(68 139)"><circle cx="60" cy="35" r="31"/><text class="flow-icon" x="60" y="40">∿</text><text class="flow-name" x="60" y="84">科学求解 Agent</text><text class="flow-role" x="60" y="100">规划工具 · 读取观察</text></g>
            <g class="flow-agent" :class="agentState('verification_critic')" transform="translate(232 139)"><circle cx="60" cy="35" r="31"/><text class="flow-icon" x="60" y="40">◇</text><text class="flow-name" x="60" y="84">验证审查 Agent</text><text class="flow-role" x="60" y="100">模型审阅 · 程序取证</text></g>
            <g class="flow-agent" :class="agentState('report_writer')" transform="translate(150 282)"><circle cx="60" cy="35" r="31"/><text class="flow-icon" x="60" y="40">✦</text><text class="flow-name" x="60" y="84">报告生成 Agent</text><text class="flow-role" x="60" y="100">汇总证据 · 独立成文</text></g>
          </svg>
          <div class="flow-caption"><span class="flow-pulse" :class="{active:busy,done:taskCompleted,cancelled:taskCancelled}"></span><b>{{ effectiveActiveAgent ? agentName(effectiveActiveAgent) : 'Agent 协作网络' }}</b><em>{{ taskCancelled ? '用户已停止生成，协作流程在当前节点取消' : taskCompleted ? 'Agent 协作已结束，结果与验证证据已归档' : (latestHandoff?.reason || currentActivity?.summary || '等待任务进入协作网络') }}</em></div>
        </div>

        <div class="team-section">
          <div class="team-title"><div><span>COLLABORATION STATUS</span><b>当前协作</b></div><em>{{ task ? `${task.agent_handoffs?.length || 0} 次交接` : '等待任务' }}</em></div>
          <section v-if="task" class="mission-card">
            <div class="mission-label"><span>当前协作</span><em v-if="busy">LIVE</em></div>
            <h3>{{ currentActivity?.title || (taskDone ? '协作任务已完成' : '正在建立任务上下文') }}</h3>
            <p>{{ currentActivity?.summary || '各 Agent 将按职责接力完成任务。' }}</p>
            <div v-if="latestHandoff" class="handoff-route"><span>{{ agentName(latestHandoff?.from) }}</span><i>→</i><span>{{ agentName(latestHandoff?.to) }}</span></div>
          </section>
          <div v-else class="team-empty"><span>◎</span><p>提交问题后，四位 Agent 会在这里接力协作。</p></div>
          <div v-if="task" class="team-metrics"><div><b>{{ task.tool_calls?.length || 0 }}</b><span>计算动作</span></div><div><b>{{ task.verification_results?.filter(v=>v.passed).length || 0 }}</b><span>验证通过</span></div><div><b>{{ task.retry_count || 0 }}</b><span>修正次数</span></div></div>
        </div>
      </aside>
    </main>

    <div v-if="showInspector" class="drawer-backdrop" @click.self="showInspector=false">
      <aside class="inspector"><header><div><span>EVENT {{ selected?.sequence }}</span><h2>{{ friendlyText(selected?.title || '') }}</h2></div><button @click="showInspector=false">×</button></header><p>{{ friendlyText(selected?.summary || '') }}</p><div class="inspector-meta"><span>{{ selected?.node || 'system' }}</span><span>{{ selected?.event_type }}</span><span v-if="selected?.duration_ms">{{ selected.duration_ms.toFixed(1) }} ms</span></div><h3>结构化载荷</h3><pre>{{ selectedPayload }}</pre></aside>
    </div>

    <div v-if="showSettings" class="popover settings-popover">
      <div class="popover-head"><b>运行设置</b><button @click="showSettings=false">×</button></div>
      <label><span>验证容差</span><input v-model.number="tolerance" type="number" step="1e-8" min="1e-14" max="0.1"></label>
      <label><span>最大重试次数</span><input v-model.number="retries" type="number" min="0" max="5"></label>
      <label class="toggle"><span>使用本地 Qwen</span><input v-model="useModel" type="checkbox"></label>
    </div>
  </div>
</template>
