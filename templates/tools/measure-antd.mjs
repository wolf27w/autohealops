import { spawn } from 'node:child_process'
// Usage: node measure-antd.mjs <seedUrl>
const url = process.argv[2]
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9335
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  `--remote-debugging-port=${PORT}`,'--remote-allow-origins=*','--user-data-dir=/tmp/chrome-cdp3','about:blank'], { stdio:'ignore' })
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
const expr = `(()=>{
  const out=[];
  const groups=[...document.querySelectorAll('.ant-layout-sider .ant-menu-submenu')];
  const roots=groups.filter(g=>!g.parentElement.closest('.ant-menu-sub'));
  roots.forEach(g=>{
    const t=g.querySelector(':scope > .ant-menu-submenu-title');
    const title=t?t.innerText.replace(/\\s+/g,' ').trim():'?';
    const items=[...g.querySelectorAll(':scope > .ant-menu-sub > .ant-menu-item, :scope > .ant-menu-sub > .ant-menu-submenu')];
    const kids=items.map(it=>{
      if(it.classList.contains('ant-menu-submenu')){
        const st=it.querySelector(':scope > .ant-menu-submenu-title');
        const leafs=[...it.querySelectorAll('.ant-menu-item')].map(x=>x.innerText.replace(/\\s+/g,' ').trim());
        return (st?st.innerText.replace(/\\s+/g,' ').trim():'?')+' -> ['+leafs.join(' / ')+']';
      }
      return it.innerText.replace(/\\s+/g,' ').trim();
    });
    out.push(title+' : '+kids.join(' | '));
  });
  const menuBox=document.querySelector('.ant-layout-sider div[style*="overflow"]')||document.querySelector('.ant-layout-sider .ant-menu');
  const menuWrap=document.querySelector('.ant-layout-sider > .ant-layout-sider-children > div:nth-child(2)');
  const items2=[...document.querySelectorAll('.ant-layout-sider .ant-menu-submenu-title, .ant-layout-sider .ant-menu-item')];
  const last=items2[items2.length-1];
  return JSON.stringify({
    groups: out,
    menuCount: items2.length,
    lastBottom: last?Math.round(last.getBoundingClientRect().bottom):null,
    viewH: window.innerHeight,
    overflow: (menuWrap? menuWrap.scrollHeight - menuWrap.clientHeight : null)
  }, null, 1);
})()`
const r = await send('Runtime.evaluate',{expression:expr,returnByValue:true},sessionId)
console.log(r.result.result.value)
proc.kill('SIGKILL'); process.exit(0)
