export type User = { id: string; username: string; role: 'owner'|'admin'|'user'; status: string; grants: Record<string,string[]> };
export type Job = {job_id:string; status:string; task_type:string; template_id:string; created_at:number; updated_at:number; progress:number; result:Record<string,unknown>|null; error:string|null; requirements:Record<string,number>};
export type Model = {id:string; task_type:string; synthetic:boolean; online:boolean};
export type Template = {id:string; name:string; kind:string; worker:string; config:Record<string,unknown>};
export type Node = {id:string; name:string; os:string; status:string; disabled:boolean; last_seen:number; policy:Record<string,unknown>; local_policy:Record<string,unknown>; capabilities:{physical:Record<string,number>; allocatable:Record<string,number>; available:Record<string,number>; cpu_load:number; memory_load:number; workers:string[]}};
export type Service = {id:string; definition_id:string; node_id:string; state:string; desired_state:string; active_requests:number; error:string|null};
export type Artifact = {id:string; name:string; download_url:string; size:number; sha256:string; expires_at:number};
let csrf = '';
export function setCsrf(value:string) { csrf=value; }
export async function api<T>(path:string, method='GET', body?:unknown):Promise<T> {
  const response=await fetch(path,{method,credentials:'same-origin', headers:{...(body===undefined?{}:{'Content-Type':'application/json'}),...(method==='GET'?{}:{'X-CSRF-Token':csrf})},body:body===undefined?undefined:JSON.stringify(body)});
  if (!response.ok) { const error=await response.json().catch(()=>null); throw new Error(error?.error?.message || `请求失败 (${response.status})`); }
  return (response.status===204?undefined:await response.json()) as T;
}
export async function streamChat(model:string,messages:{role:string;content:string}[],onContent:(content:string)=>void) {
  const response=await fetch('/v1/chat/completions',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({model,messages,stream:true})});
  if (!response.ok) {const error=await response.json().catch(()=>null);throw new Error(error?.error?.message||'推理请求失败');}
  if(!response.body) throw new Error('浏览器不支持流式响应');
  const reader=response.body.getReader(); const decoder=new TextDecoder(); let buffer='';
  try {while(true) {const {value,done}=await reader.read();buffer+=decoder.decode(value,{stream:!done});let end:number;while((end=buffer.indexOf('\n\n'))>=0) {const frame=buffer.slice(0,end);buffer=buffer.slice(end+2);for(const line of frame.split('\n')) {if(!line.startsWith('data: '))continue;const raw=line.slice(6);if(raw==='[DONE]')return;const data=JSON.parse(raw);if(data.error)throw new Error(data.error.message);onContent(data.choices?.[0]?.delta?.content||'');}}if(done)break;}}
  finally {reader.releaseLock();}
}
