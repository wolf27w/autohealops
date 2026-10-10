import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'
// 打开项目「编辑」弹窗并截图
const [url, out] = process.argv.slice(2)
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9344
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  '--hide-scrollbars', `--remote-debugging-port=${PORT}`,'--remote-allow-origins=*',
  '--user-data-dir=/tmp/chrome-cdp12','about:blank'], { stdio:'ignore' })
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
await sleep(3500)
const evalJs = async (expression) => (await send('Runtime.evaluate',{expression,returnByValue:true},sessionId)).result?.result?.value
const clickTab = async (t) => {
  await evalJs(`(()=>{const el=[...document.querySelectorAll('.ant-tabs-tab')].find(x=>x.textContent.replace(/\\s+/g,' ').includes(${JSON.stringify(t)}));if(!el)return 'NF';el.click();return 'OK'})()`)
  await sleep(900)
}
await clickTab('权限管理')
await clickTab('按项目授权')
// 点第一个「编辑」按钮（去空格匹配，避开 antd 的「编 辑」）
const clicked = await evalJs(`(()=>{const el=[...document.querySelectorAll('button')].find(x=>x.textContent.replace(/\\s+/g,'').includes('编辑'));if(!el)return 'NF';el.click();return 'OK'})()`)
console.log('edit click:', clicked)
await sleep(1200)
const labels = await evalJs(`(()=>[...document.querySelectorAll('.ant-modal .ant-form-item-label label')].map(l=>l.innerText.trim()))()`)
console.log('modal labels:', JSON.stringify(labels))
const shot = await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},sessionId)
writeFileSync(out, Buffer.from(shot.result.data,'base64'))
console.log('saved', out)
proc.kill('SIGKILL'); process.exit(0)
