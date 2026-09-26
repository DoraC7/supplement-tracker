import {blank,validate} from './core.mjs';
let db;
export async function openStore() {
  db = await new Promise((resolve,reject)=>{
    const request=indexedDB.open('supplemind-personal-v1',1);
    request.onupgradeneeded=()=>request.result.createObjectStore('state');
    request.onsuccess=()=>resolve(request.result);
    request.onerror=()=>reject(Error('無法開啟手機儲存空間，請確認不是私密瀏覽並允許網站儲存。'));
    request.onblocked=()=>reject(Error('請關閉其他 SuppleMind 分頁後重試。'));
  });
  db.onversionchange=()=>db.close();
}
export function transact(change) {
  return new Promise((resolve,reject)=>{
    const tx=db.transaction('state',change?'readwrite':'readonly');
    const store=tx.objectStore('state');
    const request=store.get('main'); let result, reason;
    request.onsuccess=()=>{try{result=validate(request.result||blank());if(change){change(result);validate(result);store.put(result,'main');}}catch(e){reason=e;tx.abort();}};
    tx.oncomplete=()=>resolve(result);
    tx.onabort=tx.onerror=()=>reject(reason||Error('儲存失敗，操作尚未完成。請先備份並檢查手機空間。'));
  });
}