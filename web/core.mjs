export const VERSION = 1;
export const blank = () => ({version: VERSION, items: [], events: []});
export const dayKey = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
export const id = () => crypto.randomUUID();
export function number(v, positive = false) {
  const n = Number(v);
  if (v === '' || !Number.isFinite(n) || n < 0 || (positive && n === 0)) throw Error('請填入有效的數量；用量須大於 0。');
  return n;
}
const text = (v, max = 2000) => typeof v === 'string' && v.length <= max;
const safeId = v => typeof v === 'string' && /^[a-zA-Z0-9_-]{1,100}$/.test(v);
const dateOK = v => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) && !isNaN(Date.parse(v)) && new Date(v).toISOString().slice(0,10) === v;
export function validate(data) {
  if (!data || data.version !== VERSION || !Array.isArray(data.items) || !Array.isArray(data.events) || data.items.length > 2000 || data.events.length > 100000) throw Error('不是支援的 SuppleMind 備份格式。');
  const ids = new Set(), eventIds = new Set(), active = new Set();
  for (const i of data.items) {
    if (!safeId(i.id) || ids.has(i.id) || !text(i.name,100) || !i.name.trim() || !text(i.unit,20) || !i.unit.trim() || !text(i.notes) || !text(i.strength,100) || !['膠囊','錠劑','粉包','液體','其他'].includes(i.form) || !['green','blue','purple','orange'].includes(i.color) || typeof i.archived !== 'boolean') throw Error('備份中的保健品資料有誤。');
    ids.add(i.id); number(i.stock); number(i.warning);
    if (typeof i.stock !== 'number' || typeof i.warning !== 'number' || typeof i.end !== 'string' || !dateOK(i.expiry) || !dateOK(i.start) || (i.end && (!dateOK(i.end) || i.end < i.start))) throw Error('日期或庫存格式錯誤。');
    if (!['daily','weekly','needed','none'].includes(i.frequency) || !Array.isArray(i.days) || i.days.some(d=>!Number.isInteger(d)||d<0||d>6) || (i.frequency==='weekly'&&!i.days.length)) throw Error('請選擇服用日。');
    if (!Array.isArray(i.slots) || i.slots.length>8 || (['daily','weekly'].includes(i.frequency)&&!i.slots.length)) throw Error('請設定至少一個時段，最多八個。');
    const times = new Set();
    for (const s of i.slots) {
      if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(s.time) || times.has(s.time) || typeof s.dose!=='number') throw Error('時間格式錯誤或重複。');
      times.add(s.time); number(s.dose,true);
    }
  }
  for (const e of data.events) {
    if (!safeId(e.id)||eventIds.has(e.id)||!ids.has(e.itemId)||!dateOK(e.day)||!text(e.at,100)||isNaN(Date.parse(e.at))||!['taken','skipped','undone'].includes(e.status)||!text(e.name,100)||!text(e.unit,20)||typeof e.dose!=='number'||!(e.slot==='needed'||/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(e.slot))) throw Error('備份中的記錄有誤。');
    eventIds.add(e.id); number(e.dose,true);
    const k = `${e.itemId}|${e.day}|${e.slot}`;
    if(e.slot!=='needed'&&e.status!=='undone'){if(active.has(k)) throw Error('備份有重複的排程記錄。');active.add(k);}
  }
  return data;
}
export function schedule(data, day = dayKey()) {
  const weekday = new Date(day+'T12:00:00').getDay();
  return data.items.filter(i=>!i.archived&&['daily','weekly'].includes(i.frequency)&&day>=i.start&&(!i.end||day<=i.end)&&(i.frequency!=='weekly'||i.days.includes(weekday)))
    .flatMap(item=>item.slots.map(s=>({item,...s,event:data.events.find(e=>e.itemId===item.id&&e.day===day&&e.slot===s.time&&e.status!=='undone')})))
    .sort((a,b)=>a.time.localeCompare(b.time)||a.item.name.localeCompare(b.item.name));
}
export function record(data, itemId, slot, status, dose, now = new Date()) {
  const day=dayKey(now), item=data.items.find(i=>i.id===itemId);
  if(!item||item.archived||!['taken','skipped'].includes(status)) throw Error('品項狀態已改變，請重新整理。');
  if(day<item.start||(item.end&&day>item.end)) throw Error('今天不在設定日期內。');
  dose=number(dose,true);
  if(slot==='needed') {if(item.frequency!=='needed'||status!=='taken') throw Error('不是需要時記錄的品項。');}
  else {const s=schedule(data,day).find(s=>s.item.id===itemId&&s.time===slot);if(!s||s.event||s.dose!==dose) throw Error('這一劑已記錄或排程已變更。');}
  if(status==='taken'){if(item.expiry<day) throw Error('此品項已過期，不能記錄服用。');if(item.stock<dose) throw Error('庫存不足，請先補貨。');item.stock-=dose;}
  data.events.push({id:id(),itemId,day,slot,status,dose,name:item.name,unit:item.unit,at:now.toISOString()});
}
export function undo(data, eventId) {
  const e=data.events.find(e=>e.id===eventId);
  if(!e||e.status==='undone') throw Error('記錄已撤銷。');
  if(e.status==='taken') data.items.find(i=>i.id===e.itemId).stock+=e.dose;
  e.status='undone'; e.undoneAt=new Date().toISOString();
}