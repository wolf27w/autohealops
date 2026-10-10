import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'
// 导航通知配置页，展开分级渠道下拉，dump 选项 + 截图
const [url, out] = process.argv.slice(2)
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9345
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  '--hide-scrollbars', `--remote-debugging-port=${PORT}`,'--remote-allow-origins=*',
  '--user-data-dir=/tmp/chrome-cdp13','about:blank'], { stdio:'ignore' })
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
await sleep(4000)
const evalJs = async (expression) => (await send('Runtime.evaluate',{expression,returnByValue:true},sessionId)).result?.result?.value
// 展开第一个分级渠道 multiple Select
const hasSection = await evalJs(`document.body.innerText.includes('分级渠道')`)
console.log('has 分级渠道 section:', hasSection)
await evalJs(`(()=>{const el=document.querySelector('.ant-select-multiple .ant-select-selector');if(!el)return 'NF';el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,view:window}));return 'OK'})()`)
await sleep(1200)
const opts = await evalJs(`(()=>[...document.querySelectorAll('.ant-select-item-option')].map(o=>o.textContent.trim()))()`)
console.log('dropdown options:', JSON.stringify(opts))
const shot = await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},sessionId)
writeFileSync(out, Buffer.from(shot.result.data,'base64'))
console.log('saved', out)
proc.kill('SIGKILL'); process.exit(0)
