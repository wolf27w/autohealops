import { spawn } from 'node:child_process'
// Usage: node measure.mjs <url>
const url = process.argv[2]
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9334
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  `--remote-debugging-port=${PORT}`,'--remote-allow-origins=*','--user-data-dir=/tmp/chrome-cdp2','about:blank'], { stdio:'ignore' })
const sleep = (ms) => new Promise(r=>setTimeout(r,ms))
async function version(){for(let i=0;i<60;i++){try{const r=await fetch(`http://127.0.0.1:${PORT}/json/version`);return await r.json()}catch{await sleep(200)}}throw new Error('no cdp')}
const v = await version()
const ws = new WebSocket(v.webSocketDebuggerUrl)
let id=0; const pending=new Map()
ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id&&pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id)}})
await new Promise(r=>ws.addEventListener('open',r))
const send=(method,params={},sessionId)=>new Promise(res=>{const i=++id;pending.set(i,res);ws.send(JSON.stringify(sessionId?{id:i,method,params,sessionId}:{id:i,method,params}))})
const {result:{targetId}}=await send('Target.createTarget',{url:'about:blank'})
const {result:{sessionId}}=await send('Target.attachToTarget',{targetId,flatten:true})
await send('Page.enable',{},sessionId)
await send('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:2,mobile:false},sessionId)
await send('Page.navigate',{url},sessionId)
await sleep(2000)
const expr = `(()=>{const n=document.querySelector('.nav');const items=[...document.querySelectorAll('.grp,.item,.sub')].map(e=>({t:e.textContent.trim(),top:e.getBoundingClientRect().top,h:e.getBoundingClientRect().height}));return JSON.stringify({navClient:n.clientHeight,navScroll:n.scrollHeight,bodyH:document.body.scrollHeight,lastItemBottom:items[items.length-1]?.top+items[items.length-1]?.h,items})})()`
const r = await send('Runtime.evaluate',{expression:expr,returnByValue:true},sessionId)
console.log(JSON.parse(r.result.result.value))
proc.kill('SIGKILL'); process.exit(0)
